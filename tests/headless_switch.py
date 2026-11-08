# ===========================================================================
# 切り替え・オペレータ・unregister のヘッドレス検証 (検証の層 6)。
#   blender -b -P tests/headless_switch.py -- [model.glb]
# 終了コード 0 = OK。結果は標準出力に JSON 1 行で出す。
# ===========================================================================
import json
import os
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DEFAULT_GLB = os.environ.get("MMD_TEST_GLB_SMALL") or "/Users/masaka1024/Desktop/IA_Conqueror_v4のコピー.glb"


def _non_identity(arm):
    return [pb.name for pb in arm.pose.bones
            if pb.matrix_basis.to_quaternion().angle > 1e-6 or pb.matrix_basis.translation.length > 1e-6]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    glb = os.path.abspath(argv[0]) if argv else DEFAULT_GLB
    res = {}
    ok = True

    def check(name, cond, detail=None):
        nonlocal ok
        res[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        for a in list(bpy.context.preferences.addons):
            if a.module.endswith("mmd2gltf_blender_importer"):
                try:
                    bpy.ops.preferences.addon_disable(module=a.module)
                except Exception:
                    pass
        import mmd2gltf_blender_importer as addon
        from mmd2gltf_blender_importer import selfphys_bake as sb, selfphys_live as live
        try:
            addon.unregister()
        except Exception:
            pass
        addon.register()
        sc = bpy.context.scene
        check("engine_default_native", sc.mmd2gltf.engine == "NATIVE", sc.mmd2gltf.engine)
        r = bpy.ops.mmd2gltf.import_glb(filepath=glb, do_physics=True, do_materials=False, do_ik=False)
        check("import", r == {"FINISHED"})
        arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        check("native_no_wiring", not [o for o in bpy.data.objects if o.get("mmd2gltf_role") in ("rigid", "joint")])
        sc.frame_start, sc.frame_end = 1, 30
        base = set(_non_identity(arm))

        # ライブ → stop で揺れ物ボーンの matrix_basis が単位に戻る
        r = bpy.ops.mmd2gltf.native_live_toggle()
        check("live_started", r == {"FINISHED"} and live.is_running(), sorted(r))
        for f in range(1, 21):
            sc.frame_set(f)
        moved = set(_non_identity(arm)) - base
        check("live_moved", len(moved) > 0, len(moved))
        r = bpy.ops.mmd2gltf.native_live_toggle()
        check("live_stopped", r == {"FINISHED"} and not live.is_running())
        left = set(_non_identity(arm)) - base
        check("stop_resets_basis", len(left) == 0, {"moved": len(moved), "left": len(left)})

        # ベイク済みでのライブ開始は FINISHED (待機の旨を報告)
        r = bpy.ops.mmd2gltf.native_bake()
        check("bake", r == {"FINISHED"} and sb.has_bake(arm), sorted(r))
        r = bpy.ops.mmd2gltf.native_live_toggle()
        check("live_idle_when_baked", r == {"FINISHED"}, sorted(r))
        live.stop()
        r = bpy.ops.mmd2gltf.native_remove_bake()
        check("remove_bake", r == {"FINISHED"} and not sb.has_bake(arm))

        # unregister でライブが止まりハンドラが消える
        r = bpy.ops.mmd2gltf.native_live_toggle()
        check("live_restart", live.is_running())
        n_handlers = len(bpy.app.handlers.frame_change_post)
        addon.unregister()
        check("unregister_stops_live", not live.is_running())
        check("unregister_handlers", live._on_frame_change not in bpy.app.handlers.frame_change_post
              and len(bpy.app.handlers.frame_change_post) < n_handlers, n_handlers)
        addon.register()
    except Exception:
        traceback.print_exc()
        ok = False
        res["exception"] = traceback.format_exc()[-600:]
    res["ok"] = ok
    print("MMD2GLTF_SWITCH " + json.dumps(res, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
