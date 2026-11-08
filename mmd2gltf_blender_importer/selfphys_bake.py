# ===========================================================================
# 自作エンジン (native) のベイク結果を Blender のアクション + NLA に書く。
#
#   native の出力: [frame][bone][7] = px,py,pz,qx,qy,qz,qw (PMX raw: 左手系 Y-up,
#   未スケール, PMX ボーン順)。全ボーン (揺れ物でないボーンも) が入っている。
#
#   アーマチュア空間のポーズ行列 (pmx_world_to_pose_matrix):
#     回転 = P · (R_world · R_rest⁻¹) · P · bone.matrix_local(3x3)
#     位置 = pmx_pos_to_blender(p, scale)
#   ローカル姿勢 (pose_bone.matrix_basis):
#     basis = rest⁻¹ · parent_rest · parent_pose⁻¹ · pose   (親が無ければ rest⁻¹ · pose)
#     rest = bone.matrix_local。parent_pose は同じフレームの native 出力から作る。
#
#   アーマチュアのオブジェクト変換: 上の計算はすべて「アーマチュア空間」で行う。
#   glTF インポーター直後は matrix_world が単位行列なので native のモデル空間と一致する。
#   利用者がオブジェクトを動かしていても、キーはアーマチュア空間の値なのでモデルごと
#   一緒に動くだけで、結果は正しい (ワールドへの換算は matrix_world を掛けるだけ)。
#
#   キーの位置: 時刻 t → フレーム t · fps / fps_base。Blender glTF インポーターも
#   同じ対応 (オフセット無し、t=0 → フレーム 0) であることをテストで確認している。
#
#   Blender のアクション API: 4.4+ の階層アクション (slots / layers / channelbag) で
#   書く。Blender 4.2–4.3 向けの旧 API (Action.fcurves) の分岐も残しているが、
#   手元に該当版が無いため未検証 (検証済み: 5.1 / 5.2.1)。
#
#   書き込み先: 新規アクション BAKE_ACTION_NAME (揺れ物ボーンの location /
#   rotation_quaternion だけ) を NLA トラック BAKE_TRACK_NAME (最上段, REPLACE) に置く。
#   注意: Blender はアクティブなアクションを NLA より上で評価する。元のアクションに
#   揺れ物ボーンの fcurve (焼き込み済み GLB など) があるとベイクが隠れるので、
#   ベイクがある間は、その揺れ物ボーンの変換 fcurve だけを mute する (キーは触らない)。
#   mute した fcurve (アクション名 + スロット + data_path + index) はアーマチュアの
#   ID プロパティ PROP_MUTED に JSON (記録のリスト) で記録し、remove_bake / 再ベイクで、
#   まだ mute のままのものだけ戻す。アクションを名前で引けないとき (改名) は、
#   アーマチュアのアクティブなアクションに記録の data_path があればそれを使う。
#   それでも見つからなければ記録を残して WARNING を出す (名前を戻せば次の remove_bake で戻る)。
#   結果の "overridden_bones" は従来どおり返す。
#
#   Blender Bullet の配線 (physics_builder のボーン拘束) が残っていると、拘束が NLA より
#   後に評価されてベイクを上書きするので、ベイク時に WARNING を出す。
# ===========================================================================
import json
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

from . import engine_bridge, glb_reader, mmd_math as mm, physics_builder

BAKE_ACTION_NAME = "MMD Physics Bake"
BAKE_TRACK_NAME = "MMD Physics Bake"
PROP_BAKE_OWNER = "mmd2gltf_bake_owner"
PROP_MUTED = "mmd2gltf_bake_muted"
_TRANSFORM_PROPS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")

_P = np.array(mm.P3, dtype=np.float64)
_INTERP_LINEAR = bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value


# ---------------------------------------------------------------------------
# 1 ボーン・1 フレーム版 (基準実装。Task 10 のリアルタイム表示でも使う)
# ---------------------------------------------------------------------------
def _pmx_quat_matrix(q7):
    """(..., qx, qy, qz, qw) の末尾 4 成分 → mathutils 3x3。"""
    return Quaternion((q7[6], q7[3], q7[4], q7[5])).normalized().to_matrix()


def pmx_world_to_pose_matrix(world7, rest7, bone, scale):
    """native のワールド姿勢 (PMX raw 7 値) → アーマチュア空間のポーズ行列 (4x4)。"""
    return pmx_world_to_pose_matrix_l3(world7, rest7, bone.matrix_local.to_3x3().normalized(), scale)


def pmx_world_to_pose_matrix_l3(world7, rest7, local3, scale):
    """pmx_world_to_pose_matrix の行列版。local3 = bone.matrix_local.to_3x3().normalized()。
    Bone を持ち越せない場面 (リアルタイム表示: 編集モードで Bone が作り直される) で使う。"""
    rel = _pmx_quat_matrix(world7) @ _pmx_quat_matrix(rest7).transposed()
    rot = mm.P3 @ rel @ mm.P3 @ local3
    m = rot.to_4x4()
    m.translation = mm.pmx_pos_to_blender(world7[:3], scale)
    return m


# ---------------------------------------------------------------------------
# 全フレーム一括版 (numpy)
# ---------------------------------------------------------------------------
def _quat_to_mat(q):
    """q: (..., 4) = (x, y, z, w) → (..., 3, 3)。"""
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def _mat_to_quat_wxyz(m):
    """(N, 3, 3) 回転行列 → (N, 4) (w, x, y, z)。Shepperd 法。"""
    n = m.shape[0]
    out = np.empty((n, 4))
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    tr = m00 + m11 + m22
    cand = np.stack([tr, m00, m11, m22], axis=1)
    k = np.argmax(cand, axis=1)
    # k == 0: w 最大
    s = k == 0
    if s.any():
        t = np.sqrt(1.0 + tr[s]) * 2
        out[s, 0] = 0.25 * t
        out[s, 1] = (m[s, 2, 1] - m[s, 1, 2]) / t
        out[s, 2] = (m[s, 0, 2] - m[s, 2, 0]) / t
        out[s, 3] = (m[s, 1, 0] - m[s, 0, 1]) / t
    s = k == 1
    if s.any():
        t = np.sqrt(1.0 + m00[s] - m11[s] - m22[s]) * 2
        out[s, 0] = (m[s, 2, 1] - m[s, 1, 2]) / t
        out[s, 1] = 0.25 * t
        out[s, 2] = (m[s, 0, 1] + m[s, 1, 0]) / t
        out[s, 3] = (m[s, 0, 2] + m[s, 2, 0]) / t
    s = k == 2
    if s.any():
        t = np.sqrt(1.0 + m11[s] - m00[s] - m22[s]) * 2
        out[s, 0] = (m[s, 0, 2] - m[s, 2, 0]) / t
        out[s, 1] = (m[s, 0, 1] + m[s, 1, 0]) / t
        out[s, 2] = 0.25 * t
        out[s, 3] = (m[s, 1, 2] + m[s, 2, 1]) / t
    s = k == 3
    if s.any():
        t = np.sqrt(1.0 + m22[s] - m00[s] - m11[s]) * 2
        out[s, 0] = (m[s, 1, 0] - m[s, 0, 1]) / t
        out[s, 1] = (m[s, 0, 2] + m[s, 2, 0]) / t
        out[s, 2] = (m[s, 1, 2] + m[s, 2, 1]) / t
        out[s, 3] = 0.25 * t
    out /= np.linalg.norm(out, axis=1, keepdims=True)
    return out


def _np_mat(m):
    return np.array(m, dtype=np.float64)


def _rigid_inv(m):
    """(N, 4, 4) の剛体変換の逆。"""
    r_t = np.swapaxes(m[:, :3, :3], 1, 2)
    out = np.zeros_like(m)
    out[:, :3, :3] = r_t
    out[:, :3, 3] = -np.einsum("nij,nj->ni", r_t, m[:, :3, 3])
    out[:, 3, 3] = 1.0
    return out


def _pose_all_frames(data, bi, rest7, bone, scale):
    """data: (F, B, 7) → このボーンのアーマチュア空間ポーズ (F, 4, 4)。"""
    f = data.shape[0]
    r_w = _quat_to_mat(data[:, bi, 3:7])
    r_r = _quat_to_mat(np.array(rest7[3:7], dtype=np.float64))
    l3 = _np_mat(bone.matrix_local.to_3x3().normalized())
    rot = _P @ (r_w @ r_r.T) @ _P @ l3
    m = np.zeros((f, 4, 4))
    m[:, :3, :3] = rot
    m[:, :3, 3] = data[:, bi][:, [0, 2, 1]] * scale
    m[:, 3, 3] = 1.0
    return m


# ---------------------------------------------------------------------------
# アクション (Blender 4.4+ の階層アクション / それ以前の両対応)
# ---------------------------------------------------------------------------
def _anim_slot(arm):
    ad = arm.animation_data
    return getattr(ad, "action_slot", None) if ad is not None else None


def action_fcurves(action, arm=None):
    """アクションの fcurve 一覧 (階層アクションでは arm のスロットのチャンネルバッグ)。"""
    if action is None:
        return []
    if hasattr(action, "layers"):
        slot = None
        if arm is not None and arm.animation_data is not None and arm.animation_data.action == action:
            slot = _anim_slot(arm)
        if slot is None and arm is not None:
            for s in action.slots:
                if s.target_id_type == "OBJECT" and (s.identifier == "OB" + arm.name
                                                     or s.name_display == arm.name):
                    slot = s
                    break
        if slot is None and len(action.slots) == 1:
            slot = action.slots[0]
        out = []
        for layer in action.layers:
            for st in layer.strips:
                cb = st.channelbag(slot) if slot is not None else None
                if cb is not None:
                    out.extend(cb.fcurves)
        return out
    return list(action.fcurves)


def _slot_fcurves(action, slot_identifier):
    """スロット識別子 (旧 API では None) を指定してアクションの fcurve 一覧を返す。"""
    if action is None:
        return []
    if hasattr(action, "layers"):
        slot = next((s for s in action.slots if s.identifier == slot_identifier), None)
        if slot is None:
            return []
        out = []
        for layer in action.layers:
            for st in layer.strips:
                cb = st.channelbag(slot)
                if cb is not None:
                    out.extend(cb.fcurves)
        return out
    return list(action.fcurves)


def _active_slot_identifier(action, arm):
    if not hasattr(action, "layers"):
        return None
    slot = _anim_slot(arm) if arm.animation_data.action == action else None
    if slot is None:
        for s in action.slots:
            if s.target_id_type == "OBJECT" and (s.identifier == "OB" + arm.name or s.name_display == arm.name):
                return s.identifier
        if len(action.slots) == 1:
            return action.slots[0].identifier
        return None
    return slot.identifier


def _muted_records(arm):
    """PROP_MUTED の記録のリスト (旧形式の 1 件 dict も受ける)。壊れていれば []。"""
    raw = arm.get(PROP_MUTED)
    if raw is None:
        return []
    try:
        rec = json.loads(raw)
    except (ValueError, TypeError):
        return []
    recs = rec if isinstance(rec, list) else [rec]
    return [r for r in recs if isinstance(r, dict)]


def _set_muted_records(arm, recs):
    if recs:
        arm[PROP_MUTED] = json.dumps(recs)
    elif PROP_MUTED in arm:
        del arm[PROP_MUTED]


def _mute_physics_curves(arm, names):
    """アクティブなアクションの、names のボーンの変換 fcurve を mute して記録する。戻り値 = 件数。"""
    ad = arm.animation_data
    act = ad.action if ad is not None else None
    if act is None:
        return 0
    prefixes = {'pose.bones["%s"].' % bpy.utils.escape_identifier(n) for n in names}
    items = []
    for fc in action_fcurves(act, arm):
        dp = fc.data_path
        if fc.mute:
            continue  # 利用者が mute 済み → 触らない
        for pre in prefixes:
            if dp.startswith(pre) and dp[len(pre):] in _TRANSFORM_PROPS:
                fc.mute = True
                items.append([dp, fc.array_index])
                break
    if items:
        recs = _muted_records(arm)   # 戻せなかった古い記録 (改名など) は残す
        recs.append({"action": act.name, "slot": _active_slot_identifier(act, arm), "curves": items})
        _set_muted_records(arm, recs)
    return len(items)


def _record_fcurves(arm, rec, want):
    """記録のアクションの fcurve 一覧。名前で引けなければ、アーマチュアのアクティブな
    アクションに記録の data_path があればそれを使う。見つからなければ None。"""
    act = bpy.data.actions.get(rec.get("action") or "")
    if act is not None:
        return _slot_fcurves(act, rec.get("slot"))
    ad = arm.animation_data
    act = ad.action if ad is not None else None
    if act is None:
        return None
    fcs = _slot_fcurves(act, rec.get("slot")) or action_fcurves(act, arm)
    if any((fc.data_path, fc.array_index) in want for fc in fcs):
        return fcs
    return None


def _unmute_physics_curves(arm, report=None):
    """記録した fcurve のうち、まだ mute のままのものを戻す。戻り値 = 戻した数。

    アクションを解決できた記録は消す。解決できない記録 (アクションの改名・削除) は残し、
    report があれば WARNING を出す。"""
    if arm.get(PROP_MUTED) is None:
        return 0
    keep = []
    n = 0
    for rec in _muted_records(arm):
        try:
            want = {(dp, int(i)) for dp, i in rec["curves"]}
        except (KeyError, TypeError, ValueError):
            continue   # 壊れた記録は捨てる
        fcs = _record_fcurves(arm, rec, want)
        if fcs is None:
            keep.append(rec)
            if report is not None:
                report("WARNING", f"mute したアクション '{rec.get('action')}' が見つからないため、"
                                  f"fcurve {len(want)} 本の mute を戻せません "
                                  "(アクション名を戻してからベイクを削除すると戻ります)")
            continue
        for fc in fcs:
            if fc.mute and (fc.data_path, fc.array_index) in want:
                fc.mute = False
                n += 1
    _set_muted_records(arm, keep)
    return n


def _new_bake_action(arm):
    act = bpy.data.actions.new(BAKE_ACTION_NAME)
    act[PROP_BAKE_OWNER] = arm.name
    if hasattr(act, "layers"):
        slot = act.slots.new(id_type="OBJECT", name=arm.name)
        layer = act.layers.new("Layer")
        strip = layer.strips.new(type="KEYFRAME")
        cb = strip.channelbag(slot, ensure=True)
        return act, slot, cb
    return act, None, act


def _new_fcurve(cb, data_path, index, group):
    if hasattr(cb, "groups"):
        g = cb.groups.get(group) if hasattr(cb.groups, "get") else None
        if g is None:
            g = cb.groups.new(group)
        fc = cb.fcurves.new(data_path, index=index)
        fc.group = g
        return fc
    return cb.fcurves.new(data_path, index=index, action_group=group)


def _write_fcurve(fc, xs, ys):
    n = len(xs)
    co = np.empty(2 * n, dtype=np.float32)
    co[0::2] = xs
    co[1::2] = ys
    fc.keyframe_points.add(n)
    fc.keyframe_points.foreach_set("co", co)
    fc.keyframe_points.foreach_set("interpolation", np.full(n, _INTERP_LINEAR, dtype=np.int32))
    fc.update()


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------
def bone_map(arm, model, nb, scale):
    """PMX index ↔ Blender ボーンの対応。戻り値 ({pmx index: Bone}, {Blender 名: pmx index})。

    find_bone の最近傍フォールバックで 2 つの PMX index が同じ Blender ボーンに
    なることがある。最初の index だけ採用し、残りは対応無し (= skipped) とする。
    ベイクとリアルタイム表示で共有する。"""
    pmx_bone = {}
    blender_to_pmx = {}
    for i in range(min(nb, len(model.bones))):
        b = physics_builder.find_bone(arm, model, i, scale)
        if b is not None and b.name not in blender_to_pmx:
            pmx_bone[i] = b
            blender_to_pmx[b.name] = i
    return pmx_bone, blender_to_pmx


def has_bake(arm):
    ad = arm.animation_data
    return ad is not None and ad.nla_tracks.get(BAKE_TRACK_NAME) is not None


def remove_bake(arm, report=None):
    """ベイクの NLA トラックとアクションを消し、mute した fcurve を戻す。何か消したら True。
    report(level, msg) を渡すと、戻せない mute の記録があるとき WARNING を出す。"""
    removed = _unmute_physics_curves(arm, report) > 0
    ad = arm.animation_data
    actions = set()
    if ad is not None:
        for tr in [t for t in ad.nla_tracks if t.name == BAKE_TRACK_NAME]:
            for st in tr.strips:
                if st.action is not None:
                    actions.add(st.action)
            ad.nla_tracks.remove(tr)
            removed = True
    for act in list(bpy.data.actions):
        if act.get(PROP_BAKE_OWNER) == arm.name:
            actions.add(act)
    for act in actions:
        if act.users == 0:
            bpy.data.actions.remove(act)
            removed = True
    return removed


def bake(context, arm, glb_path, report, progress=None):
    """native で全フレームを焼き、揺れ物ボーンのキーを NLA に書く。

    report(level, msg)。progress(done, total) は native のベイク中に呼ばれる (任意)。
    戻り値 {"frames", "bones", "skipped_bones", "nan_bone" (発散した最初のボーン名 | None),
    "overridden_bones", "muted_curves", "seconds_native", "seconds_write"}
    (アニメの無い GLB では seconds_* は無い)。
    書き込み中に例外が出たら途中までの結果を remove_bake で消してから投げ直す。"""
    scene = context.scene
    wired = physics_builder.bullet_constrained_bones(arm)
    if wired:
        report("WARNING", physics_builder.BULLET_WIRING_WARNING.format(n=len(wired)))
    model = glb_reader.load(bpy.path.abspath(glb_path))
    scale = model.unit_scale

    nm = engine_bridge.NativeModel(bpy.path.abspath(glb_path))
    try:
        info = nm.info
        nb, nf = info["bone_count"], info["frame_count"]
        result = {"frames": nf, "bones": 0, "skipped_bones": [], "nan_bone": None, "overridden_bones": [],
                  "muted_curves": 0}
        if nf == 0:
            report("ERROR", "アニメの無い GLB はリアルタイム表示を使ってください")
            return result
        phys = nm.physics_bones
        rest = nm.rest_world()
        times = np.array(nm.times(), dtype=np.float64)
        t0 = time.perf_counter()
        arr, warn = nm.bake(progress=progress)
        nan_bone = nm.last_nan_bone or warn
        result["seconds_native"] = round(time.perf_counter() - t0, 3)
    finally:
        nm.close()
    if warn:
        result["nan_bone"] = nan_bone
        report("WARNING", f"物理が発散しました (最初の検出: ボーン '{nan_bone}')")

    data = np.frombuffer(arr, dtype=np.float32).reshape(nf, nb, 7).astype(np.float64)
    fps = scene.render.fps / scene.render.fps_base
    xs = times * fps

    # PMX index ↔ Blender ボーン
    pmx_bone, blender_to_pmx = bone_map(arm, model, nb, scale)

    targets = []
    for bi in phys:
        b = pmx_bone.get(bi)
        if b is None:
            name = model.bones[bi].name if bi < len(model.bones) else str(bi)
            result["skipped_bones"].append(name)
            continue
        targets.append((bi, b))
    if result["skipped_bones"]:
        report("WARNING", f"アーマチュアに見つからないボーン {len(result['skipped_bones'])} 本をスキップ")

    # 元のアクションが揺れ物ボーンを上書きしていないか
    ad = arm.animation_data_create()
    names = {b.name for _, b in targets}
    over = set()
    for fc in action_fcurves(ad.action, arm):
        dp = fc.data_path
        if dp.startswith('pose.bones["'):
            bn = dp.split('"')[1]
            if bn in names:
                over.add(bn)
    result["overridden_bones"] = sorted(over)

    t0 = time.perf_counter()
    remove_bake(arm, report)
    try:
        _write_bake(arm, ad, data, rest, scale, xs, nf, targets, blender_to_pmx, names, over, result, report)
    except BaseException:
        try:
            remove_bake(arm)   # 途中までのアクション・NLA・mute を残さない
        except Exception:
            pass
        raise
    result["seconds_write"] = round(time.perf_counter() - t0, 3)
    return result


def _write_bake(arm, ad, data, rest, scale, xs, nf, targets, blender_to_pmx, names, over, result, report):
    """bake() の書き込み部 (アクション・fcurve・NLA・mute)。"""
    act, slot, cb = _new_bake_action(arm)

    pose_cache = {}

    def pose_of(bi, b):
        m = pose_cache.get(bi)
        if m is None:
            m = _pose_all_frames(data, bi, rest[bi], b, scale)
            pose_cache[bi] = m
        return m

    mode_changed = 0
    for bi, b in targets:
        pb = arm.pose.bones.get(b.name)
        if pb is None:
            result["skipped_bones"].append(b.name)
            continue
        if pb.rotation_mode != "QUATERNION":
            pb.rotation_mode = "QUATERNION"
            mode_changed += 1
        pose = pose_of(bi, b)
        rest_inv = _np_mat(b.matrix_local.inverted())
        par = b.parent
        pi = blender_to_pmx.get(par.name) if par is not None else None
        if pi is None:
            # 親無し。または親が PMX に無い (通常は起きない) ので親は静止姿勢のままとみなす。
            basis = rest_inv[None] @ pose
        else:
            c = rest_inv @ _np_mat(par.matrix_local)
            basis = c[None] @ _rigid_inv(pose_of(pi, par)) @ pose
        loc = basis[:, :3, 3]
        q = _mat_to_quat_wxyz(basis[:, :3, :3])
        # 符号の連続性 (LINEAR 補間で遠回りしない)
        if nf > 1:
            sgn = np.sign(np.einsum("ij,ij->i", q[1:], q[:-1]))
            sgn[sgn == 0] = 1
            q[1:] *= np.cumprod(sgn)[:, None]
        grp = b.name
        base = 'pose.bones["%s"].' % bpy.utils.escape_identifier(b.name)
        for k in range(3):
            _write_fcurve(_new_fcurve(cb, base + "location", k, grp), xs, loc[:, k])
        for k in range(4):
            _write_fcurve(_new_fcurve(cb, base + "rotation_quaternion", k, grp), xs, q[:, k])
        result["bones"] += 1

    if mode_changed:
        report("WARNING", f"揺れ物ボーン {mode_changed} 本の回転モードを QUATERNION に変更しました")

    # NLA: 最上段に REPLACE のストリップ 1 本
    track = ad.nla_tracks.new()
    track.name = BAKE_TRACK_NAME
    x0, x1 = float(xs[0]), float(xs[-1])
    if x1 <= x0:
        x1 = x0 + 1.0
    st = track.strips.new(BAKE_ACTION_NAME, int(np.floor(x0)), act)
    if slot is not None and hasattr(st, "action_slot") and st.action_slot != slot:
        st.action_slot = slot
    st.action_frame_start = x0
    st.action_frame_end = x1
    st.frame_start_ui = x0
    st.frame_end_ui = x1
    st.blend_type = "REPLACE"
    st.extrapolation = "HOLD"
    st.name = BAKE_ACTION_NAME
    # アクティブなアクションの揺れ物ボーン fcurve を mute (NLA より上で評価されベイクを隠すため)
    result["muted_curves"] = _mute_physics_curves(arm, names)
    if result["muted_curves"]:
        report("INFO", f"アクティブなアクションの揺れ物ボーン {len(over)} 本の fcurve "
                       f"{result['muted_curves']} 本を mute しました (ベイクを削除すると戻ります)")
