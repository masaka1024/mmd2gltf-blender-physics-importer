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


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print("usage: blender -b -P tests/run_headless.py -- model.glb [frames]")
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
    ok = (maxd < 1e-3) and (nan == 0) and (max_disp < height * 0.5)
    out["ok"] = ok
    print("MMD2GLTF_RESULT " + json.dumps(out, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
