# ===========================================================================
# PMX 剛体 / Joint → Blender リジッドボディ (Bullet) の配線。
#
# 設計 (Unity 版 PmxPhysicsBuilder / MmdPhysicsBehaviour に相当):
#   - 剛体 1 つ = 表示用メッシュオブジェクト 1 つ (rb_<名前>)。形状は Bullet の
#     SPHERE / BOX / CAPSULE。バウンディングボックスから形状が決まるので、
#     メッシュの寸法を PMX の寸法に厳密に合わせる。
#   - mode 0 (ボーン追従): PASSIVE + kinematic。Child Of 拘束でボーンに追従。
#       inverse_matrix = (armature @ bone.matrix_local)^-1 を手で入れる。
#       → どの姿勢でも body = boneDelta @ bodyBind になる。
#   - mode 1 (物理): ACTIVE。ボーン側に COPY_TRANSFORMS を付け、剛体の子に置いた
#       追跡 Empty (bodyBind^-1 @ boneRest を子ローカルに持つ) を写す。
#       ボーンの元アニメは REPLACE で上書きされる (Unity 版の LateUpdate 書き戻しと同じ)。
#   - mode 2 (物理+ボーン位置合わせ): 回転のみ COPY_ROTATION。位置は親ボーンから。
#   - Joint: 拘束用 Empty を Joint のワールド姿勢に置き GENERIC_SPRING。
#       PMX の制限/バネを軸入替+符号反転で写す (mmd_math 参照)。
#   - 衝突グループ: Blender の collision_collections では PMX のペア指定を
#       表現できないので、全剛体を同じ層に入れ、「衝突しない組」だけ
#       disable_collisions=True の空拘束で無効化する (mmd_tools と同じ方式)。
#       ジョイントで直接つながる組はジョイント側の disable_collisions で処理する。
#   - 重力: MMD の物理は PMX 単位で動いている。長さだけ unitScale 倍すると
#       振り子の周期が √unitScale 倍に縮む。重力も unitScale 倍して周期を揃える。
# ===========================================================================
import math

import bpy
import bmesh
from mathutils import Matrix, Vector

from . import mmd_math as mm
from . import overlap as ov

RB_COLLECTION = "MMD Physics"
RB_SUB_BODIES = "Rigid Bodies"
RB_SUB_JOINTS = "Joints"
RB_SUB_NCC = "Non-Collision"
RB_SUB_TRACK = "Bone Track"

PROP_ROLE = "mmd2gltf_role"          # "rigid" / "joint" / "ncc" / "track"
PROP_INDEX = "mmd2gltf_index"
PROP_MODE = "mmd2gltf_mode"
PROP_BONE = "mmd2gltf_bone"
PROP_GROUP = "mmd2gltf_group"
PROP_MASK = "mmd2gltf_mask"
CONSTRAINT_NAME = "mmd2gltf_physics"
PROP_OWNER = "mmd2gltf_owner"       # 生成物に書く: 対象アーマチュア名


class PhysicsOptions:
    def __init__(self):
        self.place_at_frame = True          # 動的剛体をフレーム先頭の姿勢へ整合させる
        self.ncc_distance_scale = 1.5       # 非衝突拘束を作る距離 (半径和 × この倍率)。0 で全ペア
        self.gravity_follow_scale = True    # 重力を unitScale 倍する
        self.substeps = 10
        self.solver_iterations = 20
        self.margin_ratio = 0.0             # 衝突マージン (最小寸法 × 比率)
        self.hide_track = True
        self.disable_initial_overlap = True # 静止姿勢で重なっている組の衝突を無効化
        self.overlap_shrink = 0.9           # 重なり判定時に寸法を縮める率 (かすり接触を無視)
        self.show_bodies = False            # 剛体をビューポートに表示する


def _get_or_make_collection(parent, name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in parent.children:
        try:
            parent.children.link(col)
        except RuntimeError:
            pass
    return col


def _link_only(obj, col):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)


def _capsule_mesh(name, radius, height, segments=12, rings=4):
    """Z 軸を長軸とするカプセル。全長 = height + 2·radius。"""
    bm = bmesh.new()
    half = height * 0.5
    # 上下の半球 + 円筒をリング列で構成
    ring_list = []
    # 下半球 (z = -half - r ... -half)
    for i in range(rings + 1):
        a = -math.pi / 2 + (math.pi / 2) * (i / rings)
        z = -half + radius * math.sin(a)
        rr = radius * math.cos(a)
        ring_list.append((z, rr))
    # 上半球
    for i in range(rings + 1):
        a = (math.pi / 2) * (i / rings)
        z = half + radius * math.sin(a)
        rr = radius * math.cos(a)
        ring_list.append((z, rr))
    verts_rings = []
    for z, rr in ring_list:
        if rr < 1e-9:
            verts_rings.append([bm.verts.new((0.0, 0.0, z))] * segments)
            continue
        ring = []
        for s in range(segments):
            t = 2 * math.pi * s / segments
            ring.append(bm.verts.new((rr * math.cos(t), rr * math.sin(t), z)))
        verts_rings.append(ring)
    for k in range(len(verts_rings) - 1):
        a, b = verts_rings[k], verts_rings[k + 1]
        for s in range(segments):
            v = [a[s], a[(s + 1) % segments], b[(s + 1) % segments], b[s]]
            uniq = []
            for x in v:
                if x not in uniq:
                    uniq.append(x)
            if len(uniq) >= 3:
                try:
                    bm.faces.new(uniq)
                except ValueError:
                    pass
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _sphere_mesh(name, radius):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=radius)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _box_mesh(name, half):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= half[0] * 2
        v.co.y *= half[1] * 2
        v.co.z *= half[2] * 2
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def _rb_add(obj):
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.rigidbody.object_add()
    return obj.rigid_body


def _rbc_add(obj):
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.rigidbody.constraint_add()
    return obj.rigid_body_constraint


def find_bone(arm, model, bone_index, scale):
    """PMX ボーン index → Blender Bone。名前で引き、位置で検証。無ければ最近傍。"""
    if bone_index < 0 or bone_index >= len(model.bones):
        return None
    b = model.bones[bone_index]
    exp = mm.pmx_pos_to_blender(b.world_raw, scale)
    bone = arm.data.bones.get(b.name)
    if bone is not None and (bone.matrix_local.translation - exp).length < 1e-3 * max(1.0, scale * 10):
        return bone
    best, bd = None, 1e9
    for cand in arm.data.bones:
        d = (cand.matrix_local.translation - exp).length
        if d < bd:
            best, bd = cand, d
    if bone is not None and best is not None and bd > 1e-2:
        return bone
    return best


def remove_existing(arm):
    """このアーマチュア向けに生成した物理オブジェクトを削除する (再配線用)。"""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(PROP_OWNER) == arm.name and obj.get(PROP_ROLE):
            me = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
            removed += 1
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CONSTRAINT_NAME):
                pb.constraints.remove(c)
    return removed


def bullet_constrained_bones(arm):
    """build() が揺れ物ボーンに付けた拘束 (COPY_TRANSFORMS / COPY_ROTATION、名前が
    CONSTRAINT_NAME で始まる) を持つボーン名の一覧。拘束は NLA / matrix_basis の後に
    評価されるので、残っていると自作エンジンのベイク・リアルタイム表示を上書きしてしまう。"""
    if arm is None or arm.type != "ARMATURE" or arm.pose is None:
        return []
    return [pb.name for pb in arm.pose.bones
            if any(c.name.startswith(CONSTRAINT_NAME) for c in pb.constraints)]


def has_bullet_wiring(arm):
    """このアーマチュア向けの Bullet 配線 (ボーン拘束または生成オブジェクト) が残っているか。"""
    if arm is None:
        return False
    if bullet_constrained_bones(arm):
        return True
    return any(o.get(PROP_OWNER) == arm.name and o.get(PROP_ROLE) for o in bpy.data.objects)


BULLET_WIRING_WARNING = ("Blender Bullet の配線が残っています (拘束付きボーン {n} 本)。"
                         "拘束が自作エンジンの結果を上書きするので、【1】物理の「物理を削除」を押してください")


def _layer_collection(view_layer, name):
    def walk(lc):
        if lc.collection.name == name:
            return lc
        for c in lc.children:
            r = walk(c)
            if r is not None:
                return r
        return None
    return walk(view_layer.layer_collection)


def set_helper_visibility(context, show_bodies, show_joints):
    """物理の補助コレクションの表示を切り替える (目のアイコン = 評価は継続)。"""
    for name, show in ((RB_SUB_BODIES, show_bodies), (RB_SUB_JOINTS, show_joints),
                       (RB_SUB_NCC, False), (RB_SUB_TRACK, False)):
        lc = _layer_collection(context.view_layer, name)
        if lc is not None:
            lc.hide_viewport = not show


def bake_cache(context):
    """リジッドボディのキャッシュをシーン範囲でベイクする。"""
    scene = context.scene
    rbw = scene.rigidbody_world
    if rbw is None or rbw.point_cache is None:
        return False
    rbw.point_cache.frame_start = scene.frame_start
    rbw.point_cache.frame_end = scene.frame_end
    scene.frame_set(scene.frame_start - 1)
    scene.frame_set(scene.frame_start)
    with context.temp_override(scene=scene, point_cache=rbw.point_cache):
        bpy.ops.ptcache.free_bake()
        bpy.ops.ptcache.bake(bake=True)
    return True


def _as_kinematic(rb):
    """剛体レコードの複製をボーン追従 (mode 0) にして返す。"""
    import copy
    r = copy.copy(rb)
    r.mode = 0
    return r


def _bone_pose_delta(arm, bone, scene, frame):
    """フレーム frame でのボーンのワールド変位 D = W_pose @ W_rest^-1。"""
    pb = arm.pose.bones.get(bone.name)
    if pb is None:
        return Matrix.Identity(4)
    w_pose = arm.matrix_world @ pb.matrix
    w_rest = arm.matrix_world @ bone.matrix_local
    return w_pose @ w_rest.inverted()


def build(context, arm, model, opts: PhysicsOptions, report=None):
    """物理を配線する。戻り値は統計 dict。"""
    scene = context.scene
    scale = model.unit_scale
    log = report or (lambda *a, **k: None)
    stats = {"bodies": 0, "joints": 0, "ncc": 0, "skipped_joints": 0, "dynamic": 0, "kinematic": 0}

    removed = remove_existing(arm)
    if removed:
        log("INFO", f"既存の物理オブジェクト {removed} 個を削除して再配線します。")

    if scene.rigidbody_world is None:
        bpy.ops.rigidbody.world_add()
    rbw = scene.rigidbody_world
    rbw.enabled = True
    rbw.substeps_per_frame = opts.substeps
    rbw.solver_iterations = opts.solver_iterations
    rbw.use_split_impulse = True          # 初期めり込みの弾き出しを弱める
    # キャッシュ範囲: シーン範囲とアーマチュアのアクション長の大きい方まで
    end = scene.frame_end
    ad = arm.animation_data
    if ad is not None and ad.action is not None:
        end = max(end, int(ad.action.frame_range[1]))
    for o in bpy.data.objects:
        if o.type == "MESH" and o.animation_data is None and o.data.shape_keys is not None:
            sk_ad = o.data.shape_keys.animation_data
            if sk_ad is not None and sk_ad.action is not None:
                end = max(end, int(sk_ad.action.frame_range[1]))
    if end > scene.frame_end:
        scene.frame_end = end
    if rbw.point_cache is not None:
        rbw.point_cache.frame_start = scene.frame_start
        rbw.point_cache.frame_end = scene.frame_end
    if opts.gravity_follow_scale:
        scene.gravity = (0.0, 0.0, -9.8 * scale)
        scene.use_gravity = True

    root = _get_or_make_collection(scene.collection, RB_COLLECTION)
    col_b = _get_or_make_collection(root, RB_SUB_BODIES)
    col_j = _get_or_make_collection(root, RB_SUB_JOINTS)
    col_n = _get_or_make_collection(root, RB_SUB_NCC)
    col_t = _get_or_make_collection(root, RB_SUB_TRACK)

    # フレーム先頭の姿勢 (動的剛体の初期整合用)
    place_frame = scene.frame_start
    cur_frame = scene.frame_current
    if opts.place_at_frame and place_frame != cur_frame:
        scene.frame_set(place_frame)
    context.view_layer.update()

    body_objs = []          # index → Object (None = 作れなかった)
    body_bind = []          # index → bind world matrix (Blender)
    body_placed = []        # index → 配置時の world matrix
    degenerate = 0
    # 物理ボーン (動的剛体が付くボーン) の集合。FK-rest 配置の判定に使う。
    physics_bone_names = set()
    for rb in model.rigid_bodies:
        if rb.mode != 0 and max(rb.size) > 1e-6:
            bone = find_bone(arm, model, rb.bone, scale)
            if bone is not None:
                physics_bone_names.add(bone.name)

    def anchor_delta(bone):
        """FK-rest 配置 (Unity 版 ResetBodiesToBonePoseFk と同じ考え方)。
        物理ボーン自身の姿勢 (焼き込みアニメ等) は使わず、最初の非物理祖先の変位で鎖ごと動かす。
        鎖内の剛体・ジョイントが同じ変位で動くので、ジョイントのフレームが崩れない。"""
        b = bone
        while b is not None and b.name in physics_bone_names:
            b = b.parent
        if b is None:
            return Matrix.Identity(4)
        return _bone_pose_delta(arm, b, scene, place_frame)

    for rb in model.rigid_bodies:
        bind = mm.pmx_world_matrix(rb.pos, rb.rot, scale)
        bone = find_bone(arm, model, rb.bone, scale)
        # 寸法ゼロの剛体 (ダミー)。動的にすると自由落下してボーンを引きずるので追従扱いにする。
        if max(rb.size) <= 1e-6 and rb.mode != 0:
            log("WARNING", f"剛体 '{rb.name}' は寸法 0 のため、物理演算せずボーン追従にします。")
            rb = _as_kinematic(rb)
            degenerate += 1
        placed = bind.copy()
        if bone is not None and rb.mode != 0 and opts.place_at_frame:
            placed = anchor_delta(bone) @ bind
        elif bone is not None and rb.mode == 0:
            placed = _bone_pose_delta(arm, bone, scene, place_frame) @ bind

        sx, sy, sz = rb.size
        if max(rb.size) <= 1e-6:
            sx = sy = sz = 0.05                       # 表示用の最小寸法 (PMX 単位)
        name = f"rb_{rb.name}"
        if rb.shape == 0:
            me = _sphere_mesh(name, sx * scale)
            shape = "SPHERE"
            min_dim = sx * scale
        elif rb.shape == 1:
            half = (sx * scale, sz * scale, sy * scale)   # PMX (x,y,z) → Blender (x,z,y)
            me = _box_mesh(name, half)
            shape = "BOX"
            min_dim = min(half)
        else:
            me = _capsule_mesh(name, sx * scale, sy * scale)
            shape = "CAPSULE"
            min_dim = sx * scale
        obj = bpy.data.objects.new(name, me)
        col_b.objects.link(obj)
        obj.matrix_world = placed
        obj.display_type = "WIRE"
        obj.hide_render = True
        obj[PROP_ROLE] = "rigid"
        obj[PROP_INDEX] = rb.index
        obj[PROP_MODE] = rb.mode
        obj[PROP_BONE] = bone.name if bone else ""
        obj[PROP_GROUP] = rb.group
        obj[PROP_MASK] = rb.mask
        obj[PROP_OWNER] = arm.name
        obj["mmd2gltf_name"] = rb.name

        rbo = _rb_add(obj)
        rbo.collision_shape = shape
        rbo.friction = rb.friction
        rbo.restitution = rb.restitution
        rbo.linear_damping = max(0.0, min(1.0, rb.linear_damping))
        rbo.angular_damping = max(0.0, min(1.0, rb.angular_damping))
        rbo.use_margin = True
        rbo.collision_margin = max(0.0, min_dim * opts.margin_ratio)
        rbo.use_deactivation = False
        rbo.collision_collections = [i == 0 for i in range(20)]
        if rb.mode == 0 or bone is None and rb.mode == 0:
            rbo.type = "PASSIVE"
            rbo.kinematic = True
            stats["kinematic"] += 1
        else:
            rbo.type = "ACTIVE"
            rbo.kinematic = False
            rbo.mass = max(rb.mass, 1e-4)
            stats["dynamic"] += 1

        # ---- ボーンとの結線 ----
        if bone is not None:
            if rb.mode == 0:
                # ★Blender はリジッドボディワールドの (再) 構築時に、拘束評価前の生の行列を
                #   追従剛体の位置として読む。生の行列を bind にしておくと、アニメ付きモデルでは
                #   体のコライダーがバインド位置にある前提でジョイントのフレームが作られ、
                #   再生開始と同時に髪・スカートが 1m 近く引っ張られる (実測: IA で frame2 に 0.9m)。
                #   生の行列 = 開始フレームの姿勢 (placed) にし、Child Of の逆行列もその姿勢基準に
                #   する (「逆行列を設定」と同じ)。結果は同じ boneDelta @ bind だが初回構築が安定する。
                c = obj.constraints.new("CHILD_OF")
                c.name = CONSTRAINT_NAME
                c.target = arm
                c.subtarget = bone.name
                pb = arm.pose.bones[bone.name]
                c.inverse_matrix = (arm.matrix_world @ pb.matrix).inverted()
                obj.matrix_world = placed
            else:
                # 追跡 Empty: 剛体の子。ローカル = bind^-1 @ boneRest
                tr = bpy.data.objects.new(f"track_{rb.name}", None)
                col_t.objects.link(tr)
                tr.empty_display_type = "ARROWS"
                tr.empty_display_size = max(min_dim, 0.01)
                tr.parent = obj
                tr.matrix_parent_inverse = Matrix.Identity(4)
                tr.matrix_basis = bind.inverted() @ (arm.matrix_world @ bone.matrix_local)
                tr.hide_render = True
                if opts.hide_track:
                    tr.hide_set(True)
                tr[PROP_ROLE] = "track"
                tr[PROP_OWNER] = arm.name
                pb = arm.pose.bones[bone.name]
                if rb.mode == 1:
                    c = pb.constraints.new("COPY_TRANSFORMS")
                    c.name = CONSTRAINT_NAME
                    c.target = tr
                    c.mix_mode = "REPLACE"
                    c.target_space = "WORLD"
                    c.owner_space = "WORLD"
                else:
                    c = pb.constraints.new("COPY_ROTATION")
                    c.name = CONSTRAINT_NAME + "_rot"
                    c.target = tr
                    c.mix_mode = "REPLACE"
                    c.target_space = "WORLD"
                    c.owner_space = "WORLD"
        body_objs.append(obj)
        body_bind.append(bind)
        body_placed.append(placed)
        stats["bodies"] += 1

    # ---- 静止姿勢での重なり (弾き出し防止) ----
    nr = len(body_objs)
    overlap_pairs = set()
    if opts.disable_initial_overlap:
        shapes = [ov.shape_from_rigid(model.rigid_bodies[i], body_placed[i], scale, opts.overlap_shrink)
                  for i in range(nr)]
        for i in range(nr):
            ri = model.rigid_bodies[i]
            for j in range(i + 1, nr):
                rj = model.rigid_bodies[j]
                if ri.mode == 0 and rj.mode == 0:
                    continue
                if not ri.collides_with(rj):
                    continue
                if ov.overlaps(shapes[i], shapes[j]):
                    overlap_pairs.add((i, j))
        stats["overlap"] = len(overlap_pairs)
        if overlap_pairs:
            log("INFO", f"静止姿勢で重なっている剛体ペア {len(overlap_pairs)} 組の衝突を無効化します。")

    # ---- Joint ----
    joint_pairs = set()
    for jt in model.joints:
        if not (0 <= jt.rigid_a < nr and 0 <= jt.rigid_b < nr):
            stats["skipped_joints"] += 1
            continue
        a, b = body_objs[jt.rigid_a], body_objs[jt.rigid_b]
        if a is None or b is None or a == b:
            stats["skipped_joints"] += 1
            continue
        bind = mm.pmx_world_matrix(jt.pos, jt.rot, scale)
        # 剛体 B の配置変位に追従させる (フレーム先頭整合)
        placed = body_placed[jt.rigid_b] @ body_bind[jt.rigid_b].inverted() @ bind
        em = bpy.data.objects.new(f"joint_{jt.name}", None)
        col_j.objects.link(em)
        em.empty_display_type = "ARROWS"
        em.empty_display_size = 0.05 * max(scale * 10, 0.1)
        em.matrix_world = placed
        em.hide_render = True
        em[PROP_ROLE] = "joint"
        em[PROP_INDEX] = jt.index
        em[PROP_OWNER] = arm.name
        em["mmd2gltf_name"] = jt.name
        rbc = _rbc_add(em)
        rbc.type = "GENERIC_SPRING"
        rbc.spring_type = "SPRING1"
        rbc.object1 = a
        rbc.object2 = b
        ra, rbb = model.rigid_bodies[jt.rigid_a], model.rigid_bodies[jt.rigid_b]
        rbc.disable_collisions = (not ra.collides_with(rbb)) or ((jt.rigid_a, jt.rigid_b) in overlap_pairs
                                                                 or (jt.rigid_b, jt.rigid_a) in overlap_pairs)
        rbc.use_override_solver_iterations = False
        _apply_joint_limits(rbc, jt, scale)
        key = (min(jt.rigid_a, jt.rigid_b), max(jt.rigid_a, jt.rigid_b))
        joint_pairs.add(key)
        stats["joints"] += 1

    # ---- 非衝突ペア ----
    centers = [m.translation for m in body_placed]
    radii = [rb.bounding_radius() * scale for rb in model.rigid_bodies]
    for i in range(nr):
        ri = model.rigid_bodies[i]
        oi = body_objs[i]
        if oi is None:
            continue
        for j in range(i + 1, nr):
            rj = model.rigid_bodies[j]
            oj = body_objs[j]
            if oj is None:
                continue
            if ri.mode == 0 and rj.mode == 0:
                continue                         # 静的同士は元から衝突しない
            if (i, j) in joint_pairs:
                continue                         # ジョイント側で無効化済み
            is_overlap = (i, j) in overlap_pairs
            if not is_overlap:
                if ri.collides_with(rj):
                    continue
                if opts.ncc_distance_scale > 0:
                    lim = (radii[i] + radii[j]) * opts.ncc_distance_scale
                    if (centers[i] - centers[j]).length > lim:
                        continue
            em = bpy.data.objects.new(f"ncc_{ri.name}|{rj.name}", None)
            col_n.objects.link(em)
            em.empty_display_type = "PLAIN_AXES"
            em.empty_display_size = 0.01
            em.matrix_world = Matrix.Translation((centers[i] + centers[j]) * 0.5)
            em.hide_render = True
            em[PROP_ROLE] = "ncc"
            em[PROP_OWNER] = arm.name
            em["mmd2gltf_overlap"] = is_overlap
            rbc = _rbc_add(em)
            rbc.type = "GENERIC"
            rbc.object1 = oi
            rbc.object2 = oj
            rbc.disable_collisions = True
            for ax in "xyz":
                setattr(rbc, f"use_limit_lin_{ax}", False)
                setattr(rbc, f"use_limit_ang_{ax}", False)
            em.hide_set(True)                    # 目のアイコン (評価は続く)
            stats["ncc"] += 1

    # ---- ならし ----
    # 配線直後にそのまま再生すると、リジッドボディワールドの初回構築が不整合な状態で行われ、
    # 最初の数フレームで揺れ物が 1m 近く飛ぶ (実測: IA モデルで frame2 に 0.9m)。
    # 開始フレームの 1 つ前を一度評価してから開始フレームへ戻し、キャッシュを空にすると
    # 以後は安定する (同条件で最大 2cm)。ユーザーが再生する前に必ずここを通す。
    scene.frame_set(scene.frame_start - 1)
    scene.frame_set(scene.frame_start)
    if rbw.point_cache is not None:
        with context.temp_override(scene=scene, point_cache=rbw.point_cache):
            try:
                bpy.ops.ptcache.free_bake()
            except RuntimeError:
                pass
    # 補助コレクションはビューポートで隠す (目のアイコン。シミュレーションは続く)
    set_helper_visibility(context, opts.show_bodies, False)
    arm["mmd2gltf_physics_built"] = True
    stats["degenerate"] = degenerate
    context.view_layer.update()
    return stats


def _apply_joint_limits(rbc, jt, scale):
    lin = mm.linear_limit_to_blender(jt.pos_min, jt.pos_max, scale)
    ang = mm.angular_limit_to_blender(jt.rot_min, jt.rot_max)
    # PMX Joint 種別 (0: Spring6DOF 1: 6DOF 2: P2P 3: ConeTwist 4: Slider 5: Hinge)
    t = jt.type
    if t == 2:      # P2P: 並進固定・回転自由・バネ無し
        lin = [(0.0, 0.0)] * 3
        ang = [None] * 3
    elif t == 5:    # Hinge: X 回転のみ
        lin = [(0.0, 0.0)] * 3
        ang = [ang[0], (0.0, 0.0), (0.0, 0.0)]
    elif t == 4:    # Slider: X 並進/回転のみ
        lin = [lin[0], (0.0, 0.0), (0.0, 0.0)]
        ang = [ang[0], (0.0, 0.0), (0.0, 0.0)]
    elif t == 3:    # ConeTwist: 並進固定
        lin = [(0.0, 0.0)] * 3
    for ax, lim in zip("xyz", lin):
        if lim is None:
            setattr(rbc, f"use_limit_lin_{ax}", False)
        else:
            setattr(rbc, f"use_limit_lin_{ax}", True)
            setattr(rbc, f"limit_lin_{ax}_lower", lim[0])
            setattr(rbc, f"limit_lin_{ax}_upper", lim[1])
    for ax, lim in zip("xyz", ang):
        if lim is None:
            setattr(rbc, f"use_limit_ang_{ax}", False)
        else:
            setattr(rbc, f"use_limit_ang_{ax}", True)
            setattr(rbc, f"limit_ang_{ax}_lower", lim[0])
            setattr(rbc, f"limit_ang_{ax}_upper", lim[1])
    # バネ (Spring6DOF 以外は無効)。PMX (x,y,z) → Blender (x, z, y)
    sp = mm.swap_yz(jt.spring_pos) if t == 0 else (0.0, 0.0, 0.0)
    sr = mm.swap_yz(jt.spring_rot) if t == 0 else (0.0, 0.0, 0.0)
    for ax, k in zip("xyz", sp):
        use = k > 0.0
        setattr(rbc, f"use_spring_{ax}", use)
        setattr(rbc, f"spring_stiffness_{ax}", k)
        setattr(rbc, f"spring_damping_{ax}", 0.5)
    for ax, k in zip("xyz", sr):
        use = k > 0.0
        setattr(rbc, f"use_spring_ang_{ax}", use)
        setattr(rbc, f"spring_stiffness_ang_{ax}", k)
        setattr(rbc, f"spring_damping_ang_{ax}", 0.5)
