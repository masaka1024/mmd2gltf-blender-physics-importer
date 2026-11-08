# ===========================================================================
# ヘッドレス検証: Blender をバックグラウンドで起動し、GLB を読み込んで
# 物理・材質・IK を組み、簡単な数値検査 (発散なし・ボーン位置一致) を行う。
#
# 使い方:
#   blender -b -P tests/run_headless.py -- <model.glb> [frames]
# 終了コード 0 = OK。結果は標準出力に JSON で出す。
# ===========================================================================
import json
import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def run_native(addon, glb, frames, model):
    """自作エンジン: 読み込み → native_bake → NLA の存在と NaN 無し。"""
    from mmd2gltf_blender_importer import mmd_math as mm
    bpy.context.scene.mmd2gltf.engine = "NATIVE"
    res = bpy.ops.mmd2gltf.import_glb(filepath=glb, do_physics=True, do_materials=True, do_ik=True)
    assert res == {"FINISHED"}, res
    arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
    sc = bpy.context.scene
    maxd = 0.0
    for b in model.bones:
        bone = arm.data.bones.get(b.name)
        if bone is not None:
            exp = mm.pmx_pos_to_blender(b.world_raw, model.unit_scale)
            maxd = max(maxd, (bone.matrix_local.translation - exp).length)
    # 配線不要: 剛体・ジョイントのオブジェクトが作られていないこと
    rigid = len([o for o in bpy.data.objects if o.get("mmd2gltf_role") in ("rigid", "joint", "ncc")])
    out = {"glb": os.path.basename(glb), "engine": "native", "bones": len(arm.data.bones),
           "rigid_objects": rigid, "max_bone_pos_error": maxd, "frames": frames, "engine_fallback": False}
    ok = rigid == 0 and maxd < 1e-3
    try:
        r = bpy.ops.mmd2gltf.native_bake()
        out["bake_result"] = sorted(r)
        err = None
    except RuntimeError as e:
        r, err = {"CANCELLED"}, str(e)
    if err is not None:
        out["bake_error"] = err
        out["no_anim_cancelled"] = "CANCELLED" in r and "アニメの無い GLB はリアルタイム表示を使ってください" in err
        out["ok"] = ok and out["no_anim_cancelled"]
        print("MMD2GLTF_RESULT " + json.dumps(out, ensure_ascii=False))
        return 0 if out["ok"] else 1
    from mmd2gltf_blender_importer import selfphys_bake as sb
    out["nla"] = sb.has_bake(arm)
    nan = 0
    phys = [pb.name for pb in arm.pose.bones if any(
        fc.data_path.startswith('pose.bones["%s"]' % pb.name) for fc in sb.action_fcurves(
            arm.animation_data.nla_tracks[sb.BAKE_TRACK_NAME].strips[0].action, arm))] if out["nla"] else []
    for f in range(1, frames + 1):
        sc.frame_set(f)
        for n in phys:
            m = arm.pose.bones[n].matrix
            if any(math.isnan(m[r_][c]) for r_ in range(4) for c in range(4)):
                nan += 1
    out["baked_bones"] = len(phys)
    out["nan"] = nan
    out["ok"] = ok and out["nla"] and nan == 0 and len(phys) > 0
    print("MMD2GLTF_RESULT " + json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    engine = "bullet"
    if "--engine" in argv:
        i = argv.index("--engine")
        engine = argv[i + 1].lower()
        del argv[i:i + 2]
    if not argv or engine not in ("native", "bullet"):
        print("usage: blender -b -P tests/run_headless.py -- model.glb [frames] [--engine native|bullet]")
        sys.exit(2)
    glb = os.path.abspath(argv[0])
    frames = int(argv[1]) if len(argv) > 1 else 60

    # 先に初期化し、インストール済みの拡張機能版が有効なら外す (リポジトリ版と二重登録になるため)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for a in list(bpy.context.preferences.addons):
        if a.module.endswith("mmd2gltf_blender_importer"):
            try:
                bpy.ops.preferences.addon_disable(module=a.module)
            except Exception:
                pass
    import mmd2gltf_blender_importer as addon
    try:
        addon.unregister()
    except Exception:
        pass
    addon.register()
    from mmd2gltf_blender_importer import load_model, mmd_math as mm
    model = load_model(glb, force=True)

    engine_fallback = False
    if engine == "native":
        ok_lib, reason = addon.engine_bridge.load_status()
        if ok_lib:
            sys.exit(run_native(addon, glb, frames, model))
        engine_fallback = True     # 読み込めない → Bullet 方式で続行
        print("native 不可、Bullet に切り替え: " + reason)
    bpy.context.scene.mmd2gltf.engine = "BULLET"

    res = bpy.ops.mmd2gltf.import_glb(filepath=glb, do_physics=True, do_materials=True, do_ik=True)
    assert res == {"FINISHED"}, res
    arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]

    # ボーン位置の一致
    maxd = 0.0
    for b in model.bones:
        bone = arm.data.bones.get(b.name)
        if bone is None:
            continue
        exp = mm.pmx_pos_to_blender(b.world_raw, model.unit_scale)
        maxd = max(maxd, (bone.matrix_local.translation - exp).length)

    sc = bpy.context.scene
    sc.frame_start = 1
    sc.frame_end = max(frames, 2)
    rbw = sc.rigidbody_world
    rbw.point_cache.frame_start = 1
    rbw.point_cache.frame_end = sc.frame_end
    sc.frame_set(1)
    dyn = [o for o in bpy.data.objects if o.get("mmd2gltf_role") == "rigid" and o.rigid_body.type == "ACTIVE"]
    start = {o.name: o.matrix_world.translation.copy() for o in dyn}
    max_disp = 0.0
    nan = 0
    for f in range(1, frames + 1):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for o in dyn:
            t = o.evaluated_get(dg).matrix_world.translation
            if any(math.isnan(c) for c in t):
                nan += 1
                continue
            max_disp = max(max_disp, (t - start[o.name]).length)
    height = arm.dimensions.z
    out = {
        "glb": os.path.basename(glb),
        "bones": len(arm.data.bones),
        "rigid": len([o for o in bpy.data.objects if o.get("mmd2gltf_role") == "rigid"]),
        "dynamic": len(dyn),
        "joints": len([o for o in bpy.data.objects if o.get("mmd2gltf_role") == "joint"]),
        "ncc": len([o for o in bpy.data.objects if o.get("mmd2gltf_role") == "ncc"]),
        "materials": len([m for m in bpy.data.materials if m.get("mmd2gltf_material") is not None]),
        "ik": sum(1 for pb in arm.pose.bones for c in pb.constraints if c.type == "IK"),
        "max_bone_pos_error": maxd,
        "max_dynamic_displacement": max_disp,
        "model_height": height,
        "nan": nan,
        "frames": frames,
    }
    if engine == "native":
        out["engine_fallback"] = engine_fallback
    ok = (maxd < 1e-3) and (nan == 0) and (max_disp < height * 0.5)
    out["ok"] = ok
    print("MMD2GLTF_RESULT " + json.dumps(out, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
