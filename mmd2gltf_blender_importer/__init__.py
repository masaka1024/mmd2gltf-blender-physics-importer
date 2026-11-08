# ===========================================================================
# mmd2gltf Physics Importer for Blender
#
# mmd2gltf-gui が出力した GLB を Blender に取り込み、extras.mmd から
#   【1】物理 (剛体・ジョイント → Blender リジッドボディ)
#   【2】材質 (トゥーン / スフィア / 輪郭線 / α復元)
#   【3】IK (任意)
# を再構築する。Unity 版 mmd2gltf-unity-physics-importer の Blender 移植。
# ===========================================================================
import os
import textwrap

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy_extras.io_utils import ImportHelper

from . import (engine_bridge, glb_reader, physics_builder, material_builder, ik_builder,
               selfphys_bake, selfphys_live)

PROP_GLB = "mmd2gltf_glb"
_model_cache = {}


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
def _prefs(context):
    try:
        return context.preferences.addons[__package__].preferences
    except KeyError:
        return None


def load_model(path, force=False):
    path = bpy.path.abspath(path)
    key = (path, os.path.getmtime(path) if os.path.isfile(path) else 0)
    if not force and key in _model_cache:
        return _model_cache[key]
    _model_cache.clear()
    m = glb_reader.load(path)
    _model_cache[key] = m
    return m


def find_target_armature(context):
    obj = context.active_object
    if obj is not None:
        if obj.type == "ARMATURE":
            return obj
        if obj.type == "MESH":
            if obj.parent is not None and obj.parent.type == "ARMATURE":
                return obj.parent
            for m in obj.modifiers:
                if m.type == "ARMATURE" and m.object is not None:
                    return m.object
    for o in context.selected_objects:
        if o.type == "ARMATURE":
            return o
    arms = [o for o in context.scene.objects if o.type == "ARMATURE" and o.get(PROP_GLB)]
    return arms[0] if len(arms) == 1 else None


def resolve_glb_path(context, arm):
    s = context.scene.mmd2gltf
    if arm is not None and arm.get(PROP_GLB) and os.path.isfile(bpy.path.abspath(arm[PROP_GLB])):
        return bpy.path.abspath(arm[PROP_GLB])
    if s.glb_path and os.path.isfile(bpy.path.abspath(s.glb_path)):
        return bpy.path.abspath(s.glb_path)
    return ""


def effective_engine(context):
    """実際に使うエンジン。NATIVE を選んでいても自作ライブラリが読めなければ BULLET。"""
    if context.scene.mmd2gltf.engine == "NATIVE" and engine_bridge.load_status()[0]:
        return "NATIVE"
    return "BULLET"


def _physics_opts(s):
    o = physics_builder.PhysicsOptions()
    o.place_at_frame = s.place_at_frame
    o.ncc_distance_scale = s.ncc_distance_scale
    o.gravity_follow_scale = s.gravity_follow_scale
    o.substeps = s.substeps
    o.solver_iterations = s.solver_iterations
    o.margin_ratio = s.margin_ratio
    o.hide_track = True
    o.disable_initial_overlap = s.disable_initial_overlap
    o.overlap_shrink = s.overlap_shrink
    o.show_bodies = s.show_bodies
    return o


def _material_opts(context, s):
    o = material_builder.MaterialOptions()
    o.use_orig_texture = s.use_orig_texture
    o.build_toon = s.build_toon
    o.build_sphere = s.build_sphere
    o.build_edge = s.build_edge
    o.edge_scale = s.edge_scale
    o.edge_skip_ratio = s.edge_skip_ratio
    p = _prefs(context)
    o.shared_toon_dir = p.shared_toon_dir if p else ""
    return o


class Reporter:
    """operator.report を集約しつつ、警告数を数える。"""

    def __init__(self, op):
        self.op = op
        self.warnings = 0

    def __call__(self, level, msg):
        if level in ("WARNING", "ERROR"):
            self.warnings += 1
        self.op.report({level}, msg)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
class MMD2GLTF_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    shared_toon_dir: StringProperty(
        name="共有トゥーンのフォルダ",
        description="MMD 本体同梱の toon01.bmp〜toon10.bmp を置いたフォルダ。空なら代替の階調画像を生成する",
        subtype="DIR_PATH", default="")

    def draw(self, context):
        self.layout.prop(self, "shared_toon_dir")


def _on_engine_update(self, context):
    """NATIVE 以外へ切り替えたらリアルタイム表示を止める (止めるボタンが見えなくなって取り残されるため)。"""
    if self.engine != "NATIVE" and selfphys_live.is_running():
        selfphys_live.stop()


def _wrap_lines(text, width=60, max_lines=4):
    """パネルのラベル用に折り返す (長すぎる分は最終行の末尾を … にする)。"""
    lines = textwrap.wrap(text, width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max(width - 1, 1)] + "…"
    return lines


class MMD2GLTF_SceneProps(bpy.types.PropertyGroup):
    glb_path: StringProperty(name="GLB", subtype="FILE_PATH", default="",
                             description="対象の .glb (アーマチュアに記録が無いときに使う)")
    engine: EnumProperty(
        name="物理エンジン", default="NATIVE", update=_on_engine_update,
        items=[("NATIVE", "自作エンジン", "MMD 互換の自作物理 (配線不要。ベイクまたはリアルタイム表示)"),
               ("BULLET", "Blender Bullet", "Blender のリジッドボディで再現する従来方式 (配線が必要)")])
    # physics
    place_at_frame: BoolProperty(name="先頭フレームの姿勢に合わせる", default=True,
                                 description="動的剛体をシーン開始フレームのボーン姿勢へ整合させてから配置する (MMD の物理リセット相当)")
    ncc_distance_scale: FloatProperty(name="非衝突ペアの距離倍率", default=1.5, min=0.0, soft_max=5.0,
                                      description="衝突しない組の無効化拘束を作る距離 (半径和×倍率)。0 で全ペア")
    gravity_follow_scale: BoolProperty(name="重力を unitScale に合わせる", default=True,
                                       description="重力を 9.8×unitScale にして MMD と揺れの周期を揃える")
    substeps: IntProperty(name="サブステップ", default=10, min=1, max=100,
                          description="1 フレームあたりの物理計算回数。増やすと揺れが安定するが重くなる")
    solver_iterations: IntProperty(name="ソルバ反復", default=20, min=1, max=200,
                                   description="ジョイントを解く反復回数。増やすと鎖が伸びにくくなるが重くなる")
    margin_ratio: FloatProperty(name="衝突マージン比", default=0.0, min=0.0, max=0.5,
                                description="最小寸法に対する衝突マージンの比率")
    show_bodies: BoolProperty(name="剛体を表示", default=False, description="Rigid Bodies コレクションをビューポートに表示する",
                              update=lambda self, ctx: physics_builder.set_helper_visibility(ctx, self.show_bodies, self.show_joints))
    show_joints: BoolProperty(name="ジョイントを表示", default=False, description="Joints コレクションをビューポートに表示する",
                              update=lambda self, ctx: physics_builder.set_helper_visibility(ctx, self.show_bodies, self.show_joints))
    disable_initial_overlap: BoolProperty(name="初期めり込みペアの衝突を無効化", default=True,
                                          description="静止姿勢で重なっている剛体の組は衝突させない (再生直後の弾き出し防止)")
    overlap_shrink: FloatProperty(name="めり込み判定の縮小率", default=0.9, min=0.5, max=1.0,
                                  description="重なり判定で寸法に掛ける率。小さいほど深いめり込みだけを対象にする")
    # materials
    use_orig_texture: BoolProperty(name="無加工テクスチャ (origTexture) を使う", default=True,
                                   description="α が mask / blend の材質で、GLB 内の無加工テクスチャから元のアルファを復元する")
    build_toon: BoolProperty(name="トゥーン", default=True,
                             description="トゥーン画像で陰影を段階化する (EEVEE 専用)")
    build_sphere: BoolProperty(name="スフィア", default=True,
                               description="スフィアマップ (乗算 sph / 加算 spa) を MatCap 方式で付ける")
    build_edge: BoolProperty(name="輪郭線 (Solidify)", default=True,
                             description="法線反転の Solidify で輪郭線を付ける (エッジフラグの無い材質は透明な輪郭線になる)")
    edge_scale: FloatProperty(name="輪郭線の太さ", default=0.03, min=0.0, soft_max=0.5,
                              description="edgeSize 1.0 あたりの太さ (PMX 単位)。unitScale を掛けて使う")
    edge_skip_ratio: FloatProperty(name="小物の輪郭線を省く比率", default=0.35, min=0.0, max=2.0,
                                   description="部品の最小寸法×この比率より輪郭線が厚い材質は輪郭線を付けない (0 で常に付ける)")
    # ik
    ik_limits: BoolProperty(name="IK の角度制限を写す (近似)", default=True,
                            description="PMX の IK 角度制限をボーンローカル軸の制限として近似的に写す")
    ik_prebend: FloatProperty(name="膝の初期屈曲 (度)", default=10.0, min=0.0, max=45.0,
                              description="制限付きリンクに入れる初期屈曲。0 で無効。伸び切った脚で IK が動かない問題の回避用")


# ---------------------------------------------------------------------------
# オペレータ
# ---------------------------------------------------------------------------
class MMD2GLTF_OT_import(bpy.types.Operator, ImportHelper):
    """mmd2gltf が出力した GLB を読み込み、物理・材質を再構築する"""
    bl_idname = "mmd2gltf.import_glb"
    bl_label = "mmd2gltf GLB (.glb)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".glb"
    filter_glob: StringProperty(default="*.glb;*.gltf", options={"HIDDEN"})
    do_physics: BoolProperty(name="物理を配線", default=True)
    do_materials: BoolProperty(name="材質を変換", default=True)
    do_ik: BoolProperty(name="IK を組む", default=False)
    bone_heuristic: bpy.props.EnumProperty(
        name="ボーンの向き", default="TEMPERANCE",
        items=[("BLENDER", "Blender", "Blender 標準"), ("TEMPERANCE", "Temperance", "子へ向ける (既定)"),
               ("FORTUNE", "Fortune", "実験的")])

    def draw(self, context):
        lay = self.layout
        lay.prop(self, "do_physics")
        lay.prop(self, "do_materials")
        lay.prop(self, "do_ik")
        lay.prop(self, "bone_heuristic")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        rep = Reporter(self)
        try:
            model = load_model(path, force=True)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"GLB を読めません: {e}")
            return {"CANCELLED"}
        for w in model.warnings:
            rep("WARNING", w)
        before = set(bpy.data.objects)
        try:
            bpy.ops.import_scene.gltf(filepath=path, bone_heuristic=self.bone_heuristic,
                                      import_select_created_objects=True)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"glTF インポートに失敗: {e}")
            return {"CANCELLED"}
        created = [o for o in bpy.data.objects if o not in before]
        arms = [o for o in created if o.type == "ARMATURE"]
        if not arms:
            self.report({"WARNING"}, "アーマチュアが作られませんでした (物理・IK はスキップ)。")
            return {"FINISHED"}
        arm = arms[0]
        arm[PROP_GLB] = path
        context.scene.mmd2gltf.glb_path = path
        context.view_layer.objects.active = arm
        s = context.scene.mmd2gltf
        if not model.has_mmd:
            self.report({"WARNING"}, "extras.mmd が無いため、物理・材質の再構築は行いません。")
            return {"FINISHED"}
        # 自作エンジンは配線しないので、ここでアニメ全体を再生できる範囲にしておく
        physics_builder.extend_scene_to_animation(context.scene, arm)
        if self.do_materials:
            st = material_builder.build(context, arm, model, _material_opts(context, s), rep)
            self.report({"INFO"}, f"材質 {st['materials']} 件を変換")
        if self.do_physics and effective_engine(context) == "NATIVE":
            self.report({"INFO"}, "自作エンジンを使うため物理の配線は行いません (パネルからベイクまたはリアルタイム表示)")
        elif self.do_physics:
            if s.engine == "NATIVE":
                self.report({"WARNING"}, "自作エンジンを読み込めないため Blender Bullet で配線します: "
                            + engine_bridge.load_status()[1])
            st = physics_builder.build(context, arm, model, _physics_opts(s), rep)
            self.report({"INFO"}, f"物理: 剛体 {st['bodies']} (動的 {st['dynamic']} / 追従 {st['kinematic']}), "
                                  f"ジョイント {st['joints']}, 非衝突 {st['ncc']}")
        if self.do_ik:
            n = ik_builder.build(context, arm, model, s.ik_limits, rep, s.ik_prebend)
            self.report({"INFO"}, f"IK {n} 件を構築")
        return {"FINISHED"}


class _TargetMixin:
    def _target(self, context):
        arm = find_target_armature(context)
        if arm is None:
            self.report({"ERROR"}, "対象のアーマチュアを選択してください。")
            return None, None
        path = resolve_glb_path(context, arm)
        if not path:
            self.report({"ERROR"}, "GLB のパスが分かりません。パネルの GLB 欄に指定してください。")
            return None, None
        try:
            model = load_model(path)
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"GLB を読めません: {e}")
            return None, None
        if not model.has_mmd:
            self.report({"ERROR"}, "この GLB には extras.mmd がありません (mmd2gltf の出力ではない)。")
            return None, None
        arm[PROP_GLB] = path
        return arm, model


class MMD2GLTF_OT_build_physics(bpy.types.Operator, _TargetMixin):
    """【1】物理を配線 / 再配線"""
    bl_idname = "mmd2gltf.build_physics"
    bl_label = "物理を配線 / 再配線"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm, model = self._target(context)
        if arm is None:
            return {"CANCELLED"}
        rep = Reporter(self)
        if effective_engine(context) == "NATIVE":
            self.report({"INFO"}, "自作エンジンは配線が要りません (ベイクまたはリアルタイム表示を使います)")
            return {"CANCELLED"}
        if context.scene.mmd2gltf.engine == "NATIVE":
            rep("WARNING", "自作エンジンを読み込めないため Blender Bullet で配線します: "
                + engine_bridge.load_status()[1])
        for w in model.warnings:
            rep("WARNING", w)
        st = physics_builder.build(context, arm, model, _physics_opts(context.scene.mmd2gltf), rep)
        self.report({"INFO"}, f"剛体 {st['bodies']} (動的 {st['dynamic']} / 追従 {st['kinematic']}), "
                              f"ジョイント {st['joints']} (スキップ {st['skipped_joints']}), 非衝突拘束 {st['ncc']}, "
                              f"初期めり込み {st.get('overlap', 0)} 組")
        return {"FINISHED"}


class MMD2GLTF_OT_native_bake(bpy.types.Operator):
    """自作エンジンで全フレームの物理を計算し、揺れ物ボーンを NLA にベイクする"""
    bl_idname = "mmd2gltf.native_bake"
    bl_label = "物理をベイク (自作エンジン)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = find_target_armature(context)
        if arm is None:
            self.report({"ERROR"}, "対象のアーマチュアを選択してください。")
            return {"CANCELLED"}
        path = resolve_glb_path(context, arm)
        if not path:
            self.report({"ERROR"}, "GLB のパスが分かりません。パネルの GLB 欄に指定してください。")
            return {"CANCELLED"}
        ok, reason = engine_bridge.load_status()
        if not ok:
            self.report({"ERROR"}, f"自作エンジンを読み込めません: {reason}")
            return {"CANCELLED"}
        wm = context.window_manager
        errors = []

        def rep(level, msg):
            if level == "ERROR":
                errors.append(msg)
            self.report({level}, msg)

        wm.progress_begin(0, 100)
        try:
            def progress(done, total):
                wm.progress_update(int(100 * done / max(total, 1)))

            try:
                r = selfphys_bake.bake(context, arm, path, rep, progress)
            except Exception as e:  # noqa: BLE001
                self.report({"ERROR"}, f"ベイクに失敗: {e}")
                return {"CANCELLED"}
        finally:
            wm.progress_end()
        if errors:
            return {"CANCELLED"}
        self.report({"INFO"}, f"ベイク完了: {r['frames']} フレーム / ボーン {r['bones']} 本 "
                              f"(計算 {r.get('seconds_native', 0)} 秒, 書き込み {r.get('seconds_write', 0)} 秒)")
        return {"FINISHED"}


class MMD2GLTF_OT_native_remove_bake(bpy.types.Operator):
    """自作エンジンのベイク (NLA トラックとアクション) を削除する"""
    bl_idname = "mmd2gltf.native_remove_bake"
    bl_label = "ベイクを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = find_target_armature(context)
        if arm is None:
            self.report({"ERROR"}, "対象のアーマチュアを選択してください。")
            return {"CANCELLED"}
        if selfphys_bake.remove_bake(arm, lambda level, msg: self.report({level}, msg)):
            self.report({"INFO"}, "ベイクを削除しました")
        else:
            self.report({"INFO"}, "削除するベイクはありません")
        return {"FINISHED"}


class MMD2GLTF_OT_native_live_toggle(bpy.types.Operator):
    """自作エンジンのリアルタイム表示を開始 / 停止する (フレーム変更ごとに計算)"""
    bl_idname = "mmd2gltf.native_live_toggle"
    bl_label = "リアルタイム表示"

    def execute(self, context):
        if selfphys_live.is_running():
            selfphys_live.stop()
            self.report({"INFO"}, "リアルタイム表示を止めました")
            return {"FINISHED"}
        arm = find_target_armature(context)
        if arm is None:
            self.report({"ERROR"}, "対象のアーマチュアを選択してください。")
            return {"CANCELLED"}
        path = resolve_glb_path(context, arm)
        if not path:
            self.report({"ERROR"}, "GLB のパスが分かりません。パネルの GLB 欄に指定してください。")
            return {"CANCELLED"}
        try:
            selfphys_live.start(context, arm, path, lambda level, msg: self.report({level}, msg))
        except Exception as e:  # noqa: BLE001  (RuntimeError の理由 / NativeError)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if selfphys_bake.has_bake(arm):
            self.report({"WARNING"}, "ベイク済みのため、リアルタイム表示は待機します "
                                     "(ベイクを削除すると有効になります)")
        else:
            self.report({"INFO"}, "リアルタイム表示を開始しました")
        return {"FINISHED"}


class MMD2GLTF_OT_remove_physics(bpy.types.Operator):
    """生成した物理オブジェクトと拘束を削除"""
    bl_idname = "mmd2gltf.remove_physics"
    bl_label = "物理を削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = find_target_armature(context)
        if arm is None:
            self.report({"ERROR"}, "対象のアーマチュアを選択してください。")
            return {"CANCELLED"}
        n = physics_builder.remove_existing(arm)
        arm["mmd2gltf_physics_built"] = False
        self.report({"INFO"}, f"{n} 個のオブジェクトを削除")
        return {"FINISHED"}


class MMD2GLTF_OT_build_materials(bpy.types.Operator, _TargetMixin):
    """【2】材質をトゥーン / スフィア / 輪郭線付きへ変換"""
    bl_idname = "mmd2gltf.build_materials"
    bl_label = "材質を変換"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm, model = self._target(context)
        if arm is None:
            return {"CANCELLED"}
        rep = Reporter(self)
        st = material_builder.build(context, arm, model, _material_opts(context, context.scene.mmd2gltf), rep)
        self.report({"INFO"}, f"材質 {st['materials']} 件 / メッシュ {st['meshes']} 個を変換")
        return {"FINISHED"}


class MMD2GLTF_OT_build_ik(bpy.types.Operator, _TargetMixin):
    """【3】extras.mmd の IK 定義から IK 拘束を組む"""
    bl_idname = "mmd2gltf.build_ik"
    bl_label = "IK を組む"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm, model = self._target(context)
        if arm is None:
            return {"CANCELLED"}
        rep = Reporter(self)
        s = context.scene.mmd2gltf
        n = ik_builder.build(context, arm, model, s.ik_limits, rep, s.ik_prebend)
        self.report({"INFO"}, f"IK {n} 件を構築")
        return {"FINISHED"}


class MMD2GLTF_OT_reset_cache(bpy.types.Operator):
    """リジッドボディのキャッシュを破棄して先頭フレームへ戻す"""
    bl_idname = "mmd2gltf.reset_cache"
    bl_label = "物理キャッシュをリセット"

    def execute(self, context):
        scene = context.scene
        rbw = scene.rigidbody_world
        if rbw is not None and rbw.point_cache is not None:
            with context.temp_override(scene=scene, point_cache=rbw.point_cache):
                try:
                    bpy.ops.ptcache.free_bake()
                except RuntimeError:
                    pass
            rbw.point_cache.frame_start = scene.frame_start
            rbw.point_cache.frame_end = scene.frame_end
        scene.frame_set(scene.frame_start - 1)
        scene.frame_set(scene.frame_start)
        return {"FINISHED"}


class MMD2GLTF_OT_bake_cache(bpy.types.Operator):
    """シーン範囲で物理をベイクする (途中フレームへ飛んでも結果が出るようにする)"""
    bl_idname = "mmd2gltf.bake_cache"
    bl_label = "物理をベイク"

    def execute(self, context):
        scene = context.scene
        cur = scene.frame_current
        ok = physics_builder.bake_cache(context)
        scene.frame_set(cur)
        if not ok:
            self.report({"ERROR"}, "リジッドボディワールドがありません。先に物理を配線してください。")
            return {"CANCELLED"}
        self.report({"INFO"}, f"物理をベイクしました ({scene.frame_start}〜{scene.frame_end})")
        return {"FINISHED"}


class MMD2GLTF_OT_show_info(bpy.types.Operator):
    """GLB の extras.mmd の概要をコンソールと通知に出す"""
    bl_idname = "mmd2gltf.show_info"
    bl_label = "GLB 情報"

    def execute(self, context):
        arm = find_target_armature(context)
        path = resolve_glb_path(context, arm)
        if not path:
            self.report({"ERROR"}, "GLB のパスが分かりません。")
            return {"CANCELLED"}
        m = load_model(path)
        modes = {0: 0, 1: 0, 2: 0}
        for rb in m.rigid_bodies:
            modes[rb.mode] = modes.get(rb.mode, 0) + 1
        msg = (f"{m.model_name} | unitScale {m.unit_scale} | ボーン {len(m.bones)} | 剛体 {len(m.rigid_bodies)} "
               f"(追従 {modes.get(0, 0)} / 物理 {modes.get(1, 0)} / 物理+Bone {modes.get(2, 0)}) | "
               f"ジョイント {len(m.joints)} | 材質 {len(m.materials)} | モーフ {len(m.morphs)}")
        print("[mmd2gltf]", msg)
        for w in m.warnings:
            print("[mmd2gltf] WARNING:", w)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class MMD2GLTF_PT_panel(bpy.types.Panel):
    bl_label = "mmd2gltf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "mmd2gltf"

    def draw(self, context):
        lay = self.layout
        s = context.scene.mmd2gltf
        arm = find_target_armature(context)
        box = lay.box()
        box.operator(MMD2GLTF_OT_import.bl_idname, text="GLB を読み込む", icon="IMPORT")
        if arm is not None:
            box.label(text=f"対象: {arm.name}", icon="ARMATURE_DATA")
            p = arm.get(PROP_GLB) or ""
            if p:
                box.label(text=os.path.basename(p), icon="FILE_3D")
        else:
            box.label(text="対象: (アーマチュアを選択)", icon="ERROR")
        box.prop(s, "glb_path")
        box.operator(MMD2GLTF_OT_show_info.bl_idname, icon="INFO")

        box = lay.box()
        box.label(text="【1】物理", icon="PHYSICS")
        native_ok, native_reason = engine_bridge.load_status()
        row = box.row()
        row.enabled = native_ok   # 自作エンジンが読めないときは選択不可 (Bullet 方式を表示)
        row.prop(s, "engine", expand=True)
        use_native = s.engine == "NATIVE" and native_ok
        if s.engine == "NATIVE" and not native_ok:
            col = box.column(align=True)
            col.alert = True
            col.label(text="自作エンジンを使えません (Blender Bullet 方式を表示)", icon="ERROR")
            for line in _wrap_lines(native_reason):
                col.label(text=line)
        if use_native:
            self._draw_native(context, box, arm)
        else:
            if selfphys_live.is_running():
                # エンジンを切り替えても止める手段を残す
                box.operator(MMD2GLTF_OT_native_live_toggle.bl_idname, text="リアルタイム表示を止める",
                             icon="PAUSE", depress=True)
            self._draw_bullet(context, box, s)

        box = lay.box()
        box.label(text="【2】材質", icon="MATERIAL")
        box.operator(MMD2GLTF_OT_build_materials.bl_idname, icon="NODE_MATERIAL")
        col = box.column(align=True)
        col.prop(s, "use_orig_texture")
        row = col.row(align=True)
        row.prop(s, "build_toon")
        row.prop(s, "build_sphere")
        col.prop(s, "build_edge")
        col.prop(s, "edge_scale")
        col.prop(s, "edge_skip_ratio")

        box = lay.box()
        box.label(text="【3】IK", icon="CON_KINEMATIC")
        box.operator(MMD2GLTF_OT_build_ik.bl_idname, icon="CON_KINEMATIC")
        box.prop(s, "ik_limits")
        box.prop(s, "ik_prebend")

    def _draw_native(self, context, box, arm):
        if physics_builder.has_bullet_wiring(arm):
            col = box.column(align=True)
            col.alert = True
            col.label(text="Blender Bullet の配線が残っています", icon="ERROR")
            col.label(text="(拘束が自作エンジンの結果を上書きします)")
            col.operator(MMD2GLTF_OT_remove_physics.bl_idname, icon="TRASH")
        baked = arm is not None and selfphys_bake.has_bake(arm)
        box.label(text="ベイク済み" if baked else "未ベイク", icon="CHECKMARK" if baked else "DOT")
        box.operator(MMD2GLTF_OT_native_bake.bl_idname, icon="RENDER_ANIMATION")
        box.operator(MMD2GLTF_OT_native_remove_bake.bl_idname, icon="TRASH")
        running = selfphys_live.is_running()
        box.operator(MMD2GLTF_OT_native_live_toggle.bl_idname,
                     text="リアルタイム表示を止める" if running else "リアルタイム表示",
                     icon="PAUSE" if running else "PLAY", depress=running)
        if running and baked:
            box.label(text="ベイク済みのためリアルタイム表示は待機中", icon="INFO")
        if selfphys_live.last_error():
            box.label(text="停止: " + selfphys_live.last_error()[:70], icon="ERROR")
        if selfphys_live.warning_message():
            box.label(text=selfphys_live.warning_message(), icon="ERROR")
        box.label(text="キーはベイク時のシーン fps に従います (fps を変えたら再ベイク)", icon="INFO")

    def _draw_bullet(self, context, box, s):
        row = box.row(align=True)
        row.operator(MMD2GLTF_OT_build_physics.bl_idname, icon="RIGID_BODY")
        row.operator(MMD2GLTF_OT_remove_physics.bl_idname, text="", icon="TRASH")
        row = box.row(align=True)
        row.operator(MMD2GLTF_OT_reset_cache.bl_idname, icon="FILE_REFRESH")
        row.operator(MMD2GLTF_OT_bake_cache.bl_idname, icon="RENDER_ANIMATION")
        rbw = context.scene.rigidbody_world
        if rbw is not None and rbw.point_cache is not None:
            pc = rbw.point_cache
            state = "ベイク済" if pc.is_baked else "未ベイク (先頭から再生)"
            box.label(text=f"キャッシュ {pc.frame_start}〜{pc.frame_end} / {state}", icon="PHYSICS")
        box.label(text="表示 (すぐ反映)", icon="HIDE_OFF")
        row = box.row(align=True)
        row.prop(s, "show_bodies")
        row.prop(s, "show_joints")
        box.label(text="配線設定 (次の配線で反映)", icon="PREFERENCES")
        col = box.column(align=True)
        col.prop(s, "place_at_frame")
        col.prop(s, "disable_initial_overlap")
        col.prop(s, "overlap_shrink")
        col.prop(s, "gravity_follow_scale")
        col.prop(s, "ncc_distance_scale")
        col.prop(s, "substeps")
        col.prop(s, "solver_iterations")
        col.prop(s, "margin_ratio")


def menu_import(self, context):
    self.layout.operator(MMD2GLTF_OT_import.bl_idname, text="mmd2gltf GLB (.glb) [物理・材質付き]")


classes = (
    MMD2GLTF_Preferences,
    MMD2GLTF_SceneProps,
    MMD2GLTF_OT_import,
    MMD2GLTF_OT_build_physics,
    MMD2GLTF_OT_native_bake,
    MMD2GLTF_OT_native_remove_bake,
    MMD2GLTF_OT_native_live_toggle,
    MMD2GLTF_OT_remove_physics,
    MMD2GLTF_OT_build_materials,
    MMD2GLTF_OT_build_ik,
    MMD2GLTF_OT_reset_cache,
    MMD2GLTF_OT_bake_cache,
    MMD2GLTF_OT_show_info,
    MMD2GLTF_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.mmd2gltf = PointerProperty(type=MMD2GLTF_SceneProps)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    selfphys_live.stop()   # ハンドラと native のハンドルを先に解放する
    engine_bridge.reset_status()   # 無効化・有効化でライブラリを読み直せるように
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    del bpy.types.Scene.mmd2gltf
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    _model_cache.clear()
