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
# ===========================================================================
import math
import sys
import time
from array import array

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from . import engine_bridge, glb_reader, mmd_math as mm
from . import selfphys_bake as sb

# --- 状態 (モジュール変数) ---
_nm = None            # engine_bridge.NativeModel
_arm_name = None
_last_frame = None    # 直前に処理したフレーム (None = 次は必ずリセット)
_ctx = None           # 開始時に作るボーン対応などのキャッシュ
_in_handler = False   # 再入ガード
_last_error = None    # 直近の停止理由 (Task 11 の UI 表示用)
_last_out = None      # 直近の live_step の出力 (テスト用)
_stats = {"resets": 0, "steps": 0, "handler_seconds": 0.0, "handler_calls": 0}


# ---------------------------------------------------------------------------
# 変換 (selfphys_bake.pmx_world_to_pose_matrix の逆)
# ---------------------------------------------------------------------------
def blender_pose_to_pmx_world(pose_matrix, rest7, bone, scale):
    """アーマチュア空間のポーズ行列 (4x4) → PMX raw のワールド姿勢 (px,py,pz,qx,qy,qz,qw)。

    順変換: rot = P · (R_world · R_rest⁻¹) · P · L  (L = bone.matrix_local の 3x3)
    逆変換: R_world = P · rot · L⁻¹ · P · R_rest  (P は自己逆、L は回転なので L⁻¹ = Lᵀ)"""
    rot = pose_matrix.to_3x3().normalized()
    l3 = bone.matrix_local.to_3x3().normalized()
    rel = mm.P3 @ rot @ l3.transposed() @ mm.P3
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


def start(context, arm, glb_path):
    """リアルタイム表示を始める (既に動いていれば止めてから)。失敗時は NativeError 等を投げる。"""
    global _nm, _arm_name, _last_frame, _ctx, _last_error
    stop()
    path = bpy.path.abspath(glb_path)
    nm = engine_bridge.NativeModel(path)
    try:
        ctx = _build_context(arm, nm, glb_reader.load(path))
    except BaseException:
        nm.close()
        raise
    _nm, _arm_name, _ctx = nm, arm.name, ctx
    _last_frame = None
    _last_error = None
    _add_handler(bpy.app.handlers.frame_change_post, _on_frame_change)
    _add_handler(bpy.app.handlers.load_pre, _on_load_pre)
    # 現在のフレームをすぐ反映する (リセット)
    _on_frame_change(context.scene, None)


def stop():
    """止めてハンドルを閉じる。何度呼んでもよい (アドオンの unregister() からも呼ぶ)。"""
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
    global _nm, _arm_name, _last_frame, _ctx
    nm, _nm = _nm, None
    _arm_name, _last_frame, _ctx = None, None, None
    if nm is not None:
        try:
            nm.close()
        except Exception:
            pass
    if remove_handlers:
        _remove_handler(bpy.app.handlers.frame_change_post, _on_frame_change)
        _remove_handler(bpy.app.handlers.load_pre, _on_load_pre)


def _build_context(arm, nm, model):
    info = nm.info
    nb = info["bone_count"]
    scale = model.unit_scale
    rest = nm.rest_world()
    pmx_bone, blender_to_pmx = sb.bone_map(arm, model, nb, scale)
    phys = set(nm.physics_bones)
    # 入力: (pmx index, Blender 名, Bone, rest7)。対応の無い index は静止姿勢のまま渡す。
    inputs = [(bi, b.name, b, rest[bi]) for bi, b in sorted(pmx_bone.items())]
    world_in = array("f", [0.0]) * (nb * 7)
    for bi in range(nb):
        world_in[bi * 7:bi * 7 + 7] = array("f", rest[bi])
    # 出力: 揺れ物ボーン (Blender 名, pmx index, Bone, rest7, rest⁻¹, 親の情報)
    outputs = []
    for bi in sorted(phys):
        b = pmx_bone.get(bi)
        if b is None or arm.pose.bones.get(b.name) is None:
            continue
        par = b.parent
        pi = blender_to_pmx.get(par.name) if par is not None else None
        outputs.append({
            "name": b.name, "bi": bi, "bone": b, "rest7": rest[bi],
            "rest_inv": b.matrix_local.inverted(),
            "parent": par.name if par is not None else None,
            "parent_bi": pi if pi in phys else None,   # 親も揺れ物ならその native 出力を使う
            "parent_rest": par.matrix_local.copy() if par is not None else None,
        })
    return {"nb": nb, "scale": scale, "inputs": inputs, "outputs": outputs, "world_in": world_in}


def _warn_and_stop(msg):
    global _last_error
    _last_error = msg
    print("mmd2gltf: 警告: リアルタイム物理を止めました: " + msg, file=sys.stderr)
    _stop(remove_handlers=False)


def _read_input(ctx, arm_eval):
    buf = ctx["world_in"]
    scale = ctx["scale"]
    pbs = arm_eval.pose.bones
    for bi, name, bone, rest7 in ctx["inputs"]:
        pb = pbs.get(name)
        if pb is None:
            continue
        buf[bi * 7:bi * 7 + 7] = array("f", blender_pose_to_pmx_world(pb.matrix, rest7, bone, scale))
    return buf


def _write_reset(arm, ctx):
    ident = Matrix.Identity(4)
    for o in ctx["outputs"]:
        arm.pose.bones[o["name"]].matrix_basis = ident


def _write_step(arm, arm_eval, ctx, out):
    scale = ctx["scale"]
    poses = {}
    for o in ctx["outputs"]:
        bi = o["bi"]
        w7 = out[bi * 7:bi * 7 + 7]
        if not all(math.isfinite(v) for v in w7):
            continue   # native は NaN 時に直前の姿勢を返すので通常来ない
        poses[bi] = sb.pmx_world_to_pose_matrix(w7, o["rest7"], o["bone"], scale)
    epbs = arm_eval.pose.bones
    for o in ctx["outputs"]:
        pose = poses.get(o["bi"])
        if pose is None:
            continue
        if o["parent"] is None:
            basis = o["rest_inv"] @ pose
        else:
            pp = poses.get(o["parent_bi"]) if o["parent_bi"] is not None else None
            if pp is None:
                pp = epbs[o["parent"]].matrix   # 揺れ物でない親 = このフレームの評価済み姿勢
            basis = o["rest_inv"] @ o["parent_rest"] @ _rigid_inverted(pp) @ pose
        arm.pose.bones[o["name"]].matrix_basis = basis


def _rigid_inverted(m):
    r = m.to_3x3().normalized().transposed()
    out = r.to_4x4()
    out.translation = -(r @ m.translation)
    return out


@persistent
def _on_frame_change(scene, depsgraph=None):
    global _in_handler, _last_frame, _last_out
    if _in_handler or _nm is None:
        return
    _in_handler = True
    t0 = time.perf_counter()
    try:
        arm = bpy.data.objects.get(_arm_name) if _arm_name else None
        if arm is None or arm.type != "ARMATURE":
            _warn_and_stop("アーマチュア '%s' が見つかりません" % _arm_name)
            return
        if scene.objects.get(arm.name) is None:
            return   # 別のシーンのフレーム変更
        if sb.has_bake(arm):
            _last_frame = None   # ベイクを消したら次のフレームでリセットする
            return
        ctx = _ctx
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        arm_eval = arm.evaluated_get(depsgraph)
        frame = scene.frame_current
        world_in = _read_input(ctx, arm_eval)
        if _last_frame is not None and frame == _last_frame + 1:
            r = scene.render
            dt = r.fps_base / r.fps
            out = _nm.live_step(world_in, dt)
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
    _stop(remove_handlers=False)
