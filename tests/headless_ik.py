# ===========================================================================
# IK 再構築のヘッドレス検証。
#   blender -b -P tests/headless_ik.py -- <animated.glb> [no_anim.glb]
# 終了コード 0 = OK。結果は標準出力に JSON 1 行で出す。
#
# mmd2gltf は VMD を IK 解決済みの FK アニメとして焼き込み、IK ボーンにはキーを持たない。
# その鎖に IK 拘束を組むと、動かない IK ボーンへ足が引っ張られてねじれる。
#   - アニメ付き: 「IK を組む」を有効にしても全ボーンの姿勢が IK なしと一致すること
#   - アニメ無し (任意): これまでどおり IK が組まれ、取り外すと回転モードが元に戻ること
# ===========================================================================
import json
import os
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FRAMES = (1, 100, 600, 1114, 2500)


def _setup():
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
    return addon


def _import(glb, do_ik):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.mmd2gltf.engine = "NATIVE"
    r = bpy.ops.mmd2gltf.import_glb(filepath=glb, do_physics=False, do_materials=False, do_ik=do_ik)
    assert r == {"FINISHED"}, r
    return [o for o in bpy.data.objects if o.type == "ARMATURE"][0]


def _pose(arm):
    sc = bpy.context.scene
    out = {}
    for f in FRAMES:
        if f > sc.frame_end:
            continue
        sc.frame_set(f)
        mw = arm.matrix_world
        out[f] = {pb.name: ((mw @ pb.head).copy(), (mw @ pb.tail).copy()) for pb in arm.pose.bones}
    return out


def _max_diff(a, b):
    m, worst = 0.0, None
    for f in a:
        for n in a[f]:
            d = max((a[f][n][0] - b[f][n][0]).length, (a[f][n][1] - b[f][n][1]).length)
            if d > m:
                m, worst = d, (f, n)
    return m, worst


def _ik_constraints(arm):
    return [pb.name for pb in arm.pose.bones for c in pb.constraints if c.name.startswith("mmd2gltf_ik")]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print(json.dumps({"ok": False, "error": "usage: -- <animated.glb> [no_anim.glb]"}))
        sys.exit(2)
    anim_glb = os.path.abspath(argv[0])
    noanim_glb = os.path.abspath(argv[1]) if len(argv) > 1 else None
    res = {}
    ok = True

    def check(name, cond, detail=None):
        nonlocal ok
        res[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)

    try:
        _setup()
        from mmd2gltf_blender_importer import ik_builder

        # ---- アニメ付き GLB ----
        arm = _import(anim_glb, do_ik=False)
        base = _pose(arm)
        modes = {pb.name: pb.rotation_mode for pb in arm.pose.bones}

        arm = _import(anim_glb, do_ik=True)
        d, worst = _max_diff(base, _pose(arm))
        check("anim_import_ik_keeps_pose", d < 1e-4, {"max_m": d, "at": worst})
        check("anim_import_ik_no_constraints", not _ik_constraints(arm), _ik_constraints(arm))
        changed = [n for n, m in modes.items() if arm.pose.bones[n].rotation_mode != m]
        check("anim_import_ik_rotation_modes", not changed, changed)

        # パネルの「IK を組む」でも同じ
        bpy.context.view_layer.objects.active = arm
        r = bpy.ops.mmd2gltf.build_ik()
        d, worst = _max_diff(base, _pose(arm))
        check("anim_panel_ik_keeps_pose", r == {"FINISHED"} and d < 1e-4, {"max_m": d, "at": worst})

        # ---- アニメ無し GLB (任意) ----
        if noanim_glb:
            arm = _import(noanim_glb, do_ik=False)
            modes = {pb.name: pb.rotation_mode for pb in arm.pose.bones}
            model = __import__("mmd2gltf_blender_importer").load_model(noanim_glb)
            n = ik_builder.build(bpy.context, arm, model)
            check("noanim_ik_built", n > 0 and len(_ik_constraints(arm)) == n, n)
            ik_builder.remove_existing(arm)
            changed = [nm for nm, m in modes.items() if arm.pose.bones[nm].rotation_mode != m]
            check("noanim_remove_restores_rotation_mode", not changed, changed)
    except Exception:
        check("exception", False, traceback.format_exc())

    print("MMD2GLTF_IK " + json.dumps({"ok": ok, "results": res}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


main()
