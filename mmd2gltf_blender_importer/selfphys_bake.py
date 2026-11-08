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
#   揺れ物ボーンの fcurve があるとベイクが上書きされるので、結果の
#   "overridden_bones" で知らせる (元のアクションは変更しない)。
# ===========================================================================
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

from . import engine_bridge, glb_reader, mmd_math as mm, physics_builder

BAKE_ACTION_NAME = "MMD Physics Bake"
BAKE_TRACK_NAME = "MMD Physics Bake"
PROP_BAKE_OWNER = "mmd2gltf_bake_owner"

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
    rel = _pmx_quat_matrix(world7) @ _pmx_quat_matrix(rest7).transposed()
    rot = mm.P3 @ rel @ mm.P3 @ bone.matrix_local.to_3x3().normalized()
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
def has_bake(arm):
    ad = arm.animation_data
    return ad is not None and ad.nla_tracks.get(BAKE_TRACK_NAME) is not None


def remove_bake(arm):
    """ベイクの NLA トラックとアクションを消す。何か消したら True。"""
    removed = False
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
    戻り値 {"frames", "bones", "skipped_bones", "nan_bone", "overridden_bones",
    "seconds_native", "seconds_write"}。"""
    scene = context.scene
    model = glb_reader.load(bpy.path.abspath(glb_path))
    scale = model.unit_scale

    nm = engine_bridge.NativeModel(bpy.path.abspath(glb_path))
    try:
        info = nm.info
        nb, nf = info["bone_count"], info["frame_count"]
        result = {"frames": nf, "bones": 0, "skipped_bones": [], "nan_bone": None, "overridden_bones": []}
        if nf == 0:
            report("ERROR", "アニメの無い GLB はリアルタイム表示を使ってください")
            return result
        phys = nm.physics_bones
        rest = nm.rest_world()
        times = np.array(nm.times(), dtype=np.float64)
        t0 = time.perf_counter()
        arr, warn = nm.bake(progress=progress)
        result["seconds_native"] = round(time.perf_counter() - t0, 3)
    finally:
        nm.close()
    if warn:
        result["nan_bone"] = warn
        report("WARNING", f"物理が発散しました (最初の検出: ボーン '{warn}')")

    data = np.frombuffer(arr, dtype=np.float32).reshape(nf, nb, 7).astype(np.float64)
    fps = scene.render.fps / scene.render.fps_base
    xs = times * fps

    # PMX index ↔ Blender ボーン
    pmx_bone = {}
    blender_to_pmx = {}
    for i in range(min(nb, len(model.bones))):
        b = physics_builder.find_bone(arm, model, i, scale)
        # find_bone の最近傍フォールバックで 2 つの PMX index が同じ Blender ボーンに
        # なることがある。最初の index だけ採用し、残りは対応無し (= skipped) とする。
        if b is not None and b.name not in blender_to_pmx:
            pmx_bone[i] = b
            blender_to_pmx[b.name] = i

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
    if over:
        report("WARNING", f"アクティブなアクションが揺れ物ボーン {len(over)} 本を上書きします "
                          "(NLA より上で評価されるため、ベイクが効きません)")

    t0 = time.perf_counter()
    remove_bake(arm)
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
    result["seconds_write"] = round(time.perf_counter() - t0, 3)
    return result
