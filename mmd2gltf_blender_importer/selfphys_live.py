# ===========================================================================
# 自作エンジン (native) のリアルタイム表示。
#
#   frame_change_post ハンドラで毎フレーム:
#     1. 評価済みポーズ (このフレームのアニメーション) を全ボーンについて読み、
#        blender_pose_to_pmx_world で PMX raw のワールド姿勢 [bone][7] に変換する。
#     2. 直前フレーム +1 なら live_step(dt = fps_base / fps)、それ以外 (開始・シーク・
#        逆再生・同じフレーム) は live_reset。リセット後は揺れ物ボーンの matrix_basis を
#        単位 (= 親に対して静止姿勢。native の reset が剛体を置く FK-rest と同じ) にする。
#        reset の後にウォームアップはしない (UE / Unity のライブノードと同じ)。
#     3. 揺れ物ボーンの pose_bone.matrix_basis を書き、直後に view_layer.update() を呼ぶ
#        (spec §9: frame_set とアニメーションレンダーで遅れ 0 だった唯一の方式)。
#   view_layer.update() から depsgraph_update_post や入れ子の frame_set で再入しうるので、
#   モジュール変数の再入ガードを try/finally で立てる。
#
#   入力の揺れ物ボーン: 評価済みポーズには前フレームに書いた basis が残っているが、
#   native は揺れ物ボーンの入力を使わない (reset は FK-rest で剛体を置き、step は揺れ物ボーンを
#   出力で上書きする)。非揺れ物ボーンの親としてだけ意味があるので、全ボーンの評価値を渡す。
#
#   既知の制限: 揺れ物 → 非揺れ物 → 揺れ物 の連なりは正確でない (native は真ん中の
#   非揺れ物ボーンに入力 (= 前フレームの書き込みを含む評価値) をそのまま使う)。
#   テストモデルには無い。
#
#   「MMD Physics Bake」の NLA 段がある間は何もしない (selfphys_bake.has_bake)。
#   例外は 1 回だけ警告して止める。ファイル読み込み (load_pre) とアドオン無効化 (stop) で
#   native のハンドルを閉じる。状態はモジュール変数に持つ (Blender のデータに保存しない)。
#
#   Bone (非 ID の StructRNA) はフレームをまたいで持たない。編集モードの出入りや
#   アンドゥで Bone 集合が作り直されると、持ち越した参照は解放済みメモリを指すため。
#   開始時に matrix_local と親の名前を .copy() で控え、アーマチュアは session_uid で
#   引き直す (改名にも追従)。Bone 集合が作り直されたら (簡易シグネチャで検出) 控えを作り直す。
#
#   サブフレーム (モーションブラー、frame_set(f, subframe=x)) は何もしない
#   (リセットもステップもしない。直前の整数フレームの書き込みがそのまま見える)。
#   発散 (NaN) は native が直前の姿勢を保持するので、最初のボーン名を 1 回だけ警告して続ける。
#   Blender Bullet の配線 (physics_builder のボーン拘束) が残っていると拘束が書き込みを
#   上書きするので、開始時に WARNING を出す (パネルにも「物理を削除」を出す)。
# ===========================================================================
import math
import sys
import time
from array import array

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from . import engine_bridge, glb_reader, mmd_math as mm, physics_builder
from . import selfphys_bake as sb


# --- 状態 (モジュール変数) ---
_nm = None            # engine_bridge.NativeModel
_model = None         # glb_reader のモデル (控えの作り直しに使う)
_arm_name = None
_arm_uid = None       # arm.session_uid
_last_frame = None    # 直前に処理したフレーム (None = 次は必ずリセット)
_ctx = None           # ボーン対応・行列の控え (Bone 参照は持たない)
_in_handler = False   # 再入ガード
_last_error = None    # 直近の停止理由 (Task 11 の UI 表示用)
_last_warning = None  # 直近の発散で最初に検出したボーン名 (1 回だけ出す)
_last_out = None      # 直近の live_step の出力 (テスト用)
_stats = {"resets": 0, "steps": 0, "rebuilds": 0, "handler_seconds": 0.0, "handler_calls": 0}


# ---------------------------------------------------------------------------
# 変換 (selfphys_bake.pmx_world_to_pose_matrix の逆)
# ---------------------------------------------------------------------------
def blender_pose_to_pmx_world(pose_matrix, rest7, bone, scale):
    """アーマチュア空間のポーズ行列 (4x4) → PMX raw のワールド姿勢 (px,py,pz,qx,qy,qz,qw)。

    順変換: rot = P · (R_world · R_rest⁻¹) · P · L  (L = bone.matrix_local の 3x3)
    逆変換: R_world = P · rot · L⁻¹ · P · R_rest  (P は自己逆、L は回転なので L⁻¹ = Lᵀ)"""
    return blender_pose_to_pmx_world_l3(pose_matrix, rest7, bone.matrix_local.to_3x3().normalized(), scale)


def blender_pose_to_pmx_world_l3(pose_matrix, rest7, local3, scale):
    """blender_pose_to_pmx_world の行列版。local3 = bone.matrix_local.to_3x3().normalized()。"""
    rot = pose_matrix.to_3x3().normalized()
    rel = mm.P3 @ rot @ local3.transposed() @ mm.P3
    w = rel @ sb._pmx_quat_matrix(rest7)
    q = w.to_quaternion()
    t = pose_matrix.translation
    return (t[0] / scale, t[2] / scale, t[1] / scale, q.x, q.y, q.z, q.w)


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------
def is_running():
    return _nm is not None


def last_error():
    return _last_error


def last_warning():
    """発散した最初のボーン名 (無ければ None)。"""
    return _last_warning


def warning_message():
    """パネル表示用の発散メッセージ (無ければ None)。"""
    if _last_warning is None:
        return None
    return f"物理が発散しました (最初の検出: ボーン '{_last_warning}')"


def start(context, arm, glb_path, report=None):
    """リアルタイム表示を始める (既に動いていれば止めてから)。

    失敗時は例外を投げる (モデル読み込みは NativeError 等、最初のフレームの処理で
    止まった場合は RuntimeError(理由))。正常に戻れば is_running() は True。
    report(level, msg) を渡すと、Bullet の配線が残っているとき WARNING を出す。"""
    global _nm, _model, _arm_name, _arm_uid, _last_frame, _ctx, _last_error, _last_warning
    stop()
    wired = physics_builder.bullet_constrained_bones(arm)
    if wired:
        msg = physics_builder.BULLET_WIRING_WARNING.format(n=len(wired))
        if report is not None:
            report("WARNING", msg)
        else:
            print("mmd2gltf: 警告: " + msg, file=sys.stderr)
    path = bpy.path.abspath(glb_path)
    nm = engine_bridge.NativeModel(path)
    try:
        model = glb_reader.load(path)
        ctx = _build_context(arm, nm, model)
    except BaseException:
        nm.close()
        raise
    _nm, _model, _ctx = nm, model, ctx
    _arm_name, _arm_uid = arm.name, arm.session_uid
    _last_frame = None
    _last_error = None
    _last_warning = None
    _add_handler(bpy.app.handlers.frame_change_post, _on_frame_change)
    _add_handler(bpy.app.handlers.load_pre, _on_load_pre)
    # 現在のフレームをすぐ反映する (リセット)
    _on_frame_change(context.scene, None)
    if not is_running():
        stop()
        raise RuntimeError(_last_error or "リアルタイム物理を開始できませんでした")


def stop():
    """止めてハンドルを閉じる。何度呼んでもよい (アドオンの unregister() からも呼ぶ)。
    アーマチュアが残っていれば揺れ物ボーンの matrix_basis を単位に戻す
    (保存した .blend に最後のシミュレーション姿勢が残らないように)。"""
    if _nm is not None and _ctx is not None:
        try:
            arm = _find_armature()
            if arm is not None and arm.type == "ARMATURE":
                _write_reset(arm, _ctx)
        except Exception:
            pass
    _stop(remove_handlers=True)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _add_handler(lst, fn):
    if fn not in lst:
        lst.append(fn)


def _remove_handler(lst, fn):
    while fn in lst:
        lst.remove(fn)


def _stop(remove_handlers):
    """ハンドラ実行中に呼ぶときは remove_handlers=False にする (Blender がハンドラ一覧を
    添字で回しているため、実行中に取り除くと他のアドオンのハンドラが 1 つ飛ばされる)。
    残ったハンドラは _nm が None なので何もしない。"""
    global _nm, _model, _arm_name, _arm_uid, _last_frame, _ctx
    nm, _nm = _nm, None
    _model, _arm_name, _arm_uid, _last_frame, _ctx = None, None, None, None, None
    if nm is not None:
        try:
            nm.close()
        except Exception:
            pass
    if remove_handlers:
        _remove_handler(bpy.app.handlers.frame_change_post, _on_frame_change)
        _remove_handler(bpy.app.handlers.load_pre, _on_load_pre)


def _bones_signature(arm):
    """Bone 集合が作り直されたか (編集モード・アンドゥ) を安く見分ける。
    Bone の構造体アドレスは作り直しで変わる。ここで触る Bone はその場で引いたものだけ。"""
    bones = arm.data.bones
    n = len(bones)
    if n == 0:
        return (arm.data.session_uid, 0)
    return (arm.data.session_uid, n, bones[0].as_pointer(), bones[n - 1].as_pointer())


def _build_context(arm, nm, model):
    info = nm.info
    nb = info["bone_count"]
    scale = model.unit_scale
    rest = nm.rest_world()
    pmx_bone, blender_to_pmx = sb.bone_map(arm, model, nb, scale)
    phys = set(nm.physics_bones)
    # 入力: (pmx index, Blender 名, local3, rest7)。対応の無い index は静止姿勢のまま渡す。
    inputs = [(bi, b.name, b.matrix_local.to_3x3().normalized(), rest[bi])
              for bi, b in sorted(pmx_bone.items())]
    world_in = array("f", [0.0]) * (nb * 7)
    for bi in range(nb):
        world_in[bi * 7:bi * 7 + 7] = array("f", rest[bi])
    # 出力: 揺れ物ボーン。行列はすべてコピー (Bone 参照は残さない)。
    outputs = []
    for bi in sorted(phys):
        b = pmx_bone.get(bi)
        if b is None or arm.pose.bones.get(b.name) is None:
            continue
        par = b.parent
        pi = blender_to_pmx.get(par.name) if par is not None else None
        outputs.append({
            "name": b.name, "bi": bi, "rest7": rest[bi],
            "local3": b.matrix_local.to_3x3().normalized(),
            "rest_inv": b.matrix_local.inverted(),
            "parent": par.name if par is not None else None,
            "parent_bi": pi if pi in phys else None,   # 親も揺れ物ならその native 出力を使う
            "parent_rest": par.matrix_local.copy() if par is not None else None,
        })
    return {"nb": nb, "scale": scale, "inputs": inputs, "outputs": outputs, "world_in": world_in,
            "sig": _bones_signature(arm)}


def _find_armature():
    """記録した session_uid のアーマチュア。改名されていれば名前を更新する。無ければ None。"""
    global _arm_name
    obj = bpy.data.objects.get(_arm_name) if _arm_name else None
    if obj is not None and obj.session_uid == _arm_uid:
        return obj
    for o in bpy.data.objects:
        if o.session_uid == _arm_uid:
            _arm_name = o.name
            return o
    return None


def _warn_and_stop(msg):
    global _last_error
    _last_error = msg
    print("mmd2gltf: 警告: リアルタイム物理を止めました: " + msg, file=sys.stderr)
    _stop(remove_handlers=False)


def _read_input(ctx, arm_eval):
    buf = ctx["world_in"]
    scale = ctx["scale"]
    pbs = arm_eval.pose.bones
    for bi, name, local3, rest7 in ctx["inputs"]:
        pb = pbs.get(name)
        if pb is None:
            continue
        buf[bi * 7:bi * 7 + 7] = array("f", blender_pose_to_pmx_world_l3(pb.matrix, rest7, local3, scale))
    return buf


def _write_reset(arm, ctx):
    ident = Matrix.Identity(4)
    pbs = arm.pose.bones
    for o in ctx["outputs"]:
        pb = pbs.get(o["name"])
        if pb is not None:
            pb.matrix_basis = ident


def _write_step(arm, arm_eval, ctx, out):
    scale = ctx["scale"]
    poses = {}
    for o in ctx["outputs"]:
        bi = o["bi"]
        w7 = out[bi * 7:bi * 7 + 7]
        if not all(math.isfinite(v) for v in w7):
            continue   # native は NaN 時に直前の姿勢を返すので通常来ない
        poses[bi] = sb.pmx_world_to_pose_matrix_l3(w7, o["rest7"], o["local3"], scale)
    epbs = arm_eval.pose.bones
    pbs = arm.pose.bones
    for o in ctx["outputs"]:
        pose = poses.get(o["bi"])
        pb = pbs.get(o["name"])
        if pose is None or pb is None:
            continue
        if o["parent"] is None:
            basis = o["rest_inv"] @ pose
        else:
            pp = poses.get(o["parent_bi"]) if o["parent_bi"] is not None else None
            if pp is None:
                epb = epbs.get(o["parent"])
                if epb is None:
                    continue
                pp = epb.matrix   # 揺れ物でない親 = このフレームの評価済み姿勢
            basis = o["rest_inv"] @ o["parent_rest"] @ _rigid_inverted(pp) @ pose
        pb.matrix_basis = basis


def _rigid_inverted(m):
    r = m.to_3x3().normalized().transposed()
    out = r.to_4x4()
    out.translation = -(r @ m.translation)
    return out


def _is_subframe(scene):
    ff = scene.frame_current_final
    return abs(ff - round(ff)) > 1e-6


@persistent
def _on_frame_change(scene, depsgraph=None):
    global _in_handler, _last_frame, _last_out, _ctx, _last_warning
    if _in_handler or _nm is None:
        return
    _in_handler = True
    t0 = time.perf_counter()
    try:
        arm = _find_armature()
        if arm is None or arm.type != "ARMATURE":
            _warn_and_stop("アーマチュア '%s' が見つかりません" % _arm_name)
            return
        if scene.objects.get(arm.name) is None:
            return   # 別のシーンのフレーム変更
        if arm.mode == "EDIT":
            _last_frame = None   # 編集中のポーズは使えない。抜けたらリセットから
            return
        if _is_subframe(scene):
            return   # サブフレームはリセットもステップもしない
        if sb.has_bake(arm):
            _last_frame = None   # ベイクを消したら次のフレームでリセットする
            return
        ctx = _ctx
        if ctx["sig"] != _bones_signature(arm):
            ctx = _ctx = _build_context(arm, _nm, _model)
            _stats["rebuilds"] += 1
            _last_frame = None
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        arm_eval = arm.evaluated_get(depsgraph)
        frame = scene.frame_current
        world_in = _read_input(ctx, arm_eval)
        if _last_frame is not None and frame == _last_frame + 1:
            r = scene.render
            dt = r.fps_base / r.fps
            out = _nm.live_step(world_in, dt)
            if _nm.live_warning and _last_warning is None:
                _last_warning = _nm.last_nan_bone or _nm.live_warning
                print("mmd2gltf: 警告: 物理が発散しました (直前の姿勢を保持): " + _nm.live_warning,
                      file=sys.stderr)
            _write_step(arm, arm_eval, ctx, out)
            _last_out = out
            _stats["steps"] += 1
        else:
            _nm.live_reset(world_in)
            _write_reset(arm, ctx)
            _stats["resets"] += 1
        _last_frame = frame
        bpy.context.view_layer.update()
    except Exception as e:
        _warn_and_stop("%s: %s" % (type(e).__name__, e))
    finally:
        _stats["handler_seconds"] += time.perf_counter() - t0
        _stats["handler_calls"] += 1
        _in_handler = False


@persistent
def _on_load_pre(*_args):
    # 読み込み中なのでハンドラ一覧はここでは触らない (残っても _nm が None なので無害)。
    global _last_error, _last_warning
    _stop(remove_handlers=False)
    _last_error = None     # 前のファイルの停止理由・発散警告を新しいファイルに持ち越さない
    _last_warning = None
