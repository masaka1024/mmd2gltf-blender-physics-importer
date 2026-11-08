# ===========================================================================
# 焼き込み済み GLB (アクティブなアクションが揺れ物ボーンにキーを持つ) でも
# ベイクが効くことの検証。ベイク中は揺れ物ボーンの変換 fcurve を mute する。
#   blender -b -P tests/headless_selfphys_override.py -- [prebaked.glb]
# ===========================================================================
import json
import math
import os
import sys
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DEFAULT_GLB = os.environ.get("MMD_TEST_GLB_PREBAKED") or "/Users/masaka1024/Downloads/IA 2/IA.glb"
POS_TOL = 1e-4
RESTORE_TOL = 1e-5
TRANSFORM = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    glb = os.path.abspath(argv[0]) if argv else DEFAULT_GLB
    res, ok = {}, True

    def check(name, cond, detail=None):
        nonlocal ok
        res[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=glb, bone_heuristic="TEMPERANCE")
        arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        arm["mmd2gltf_glb"] = glb
        sc = bpy.context.scene
        sc.render.fps, sc.render.fps_base = 24, 1.0

        from mmd2gltf_blender_importer import selfphys_bake as sb
        from mmd2gltf_blender_importer import engine_bridge as eb
        from mmd2gltf_blender_importer import glb_reader, physics_builder, mmd_math as mm

        model = glb_reader.load(glb)
        scale = model.unit_scale
        nm = eb.NativeModel(glb)
        nb, nf = nm.info["bone_count"], nm.info["frame_count"]
        phys = nm.physics_bones
        times = nm.times()
        arr, _ = nm.bake()
        nm.close()
        bones = [(bi, physics_builder.find_bone(arm, model, bi, scale).name) for bi in phys]
        names = {n for _, n in bones}

        act = arm.animation_data.action
        fcs = sb.action_fcurves(act, arm)

        def is_phys_tf(fc):
            dp = fc.data_path
            if not dp.startswith('pose.bones["'):
                return False
            return dp.split('"')[1] in names and dp.rsplit(".", 1)[-1] in TRANSFORM

        tf = [fc for fc in fcs if is_phys_tf(fc)]
        keys0 = sorted((fc.data_path, fc.array_index, len(fc.keyframe_points)) for fc in fcs)
        check("prebaked_has_physics_keys", len(tf) > 0, len(tf))
        check("none_muted_initially", not any(fc.mute for fc in fcs))

        fps = sc.render.fps / sc.render.fps_base
        idxs = sorted(set([0, 1, nf // 3, nf // 2, nf - 2, nf - 1]))

        def sample():
            out = {}
            for i in idxs:
                x = times[i] * fps
                fi = math.floor(x)
                sc.frame_set(fi, subframe=x - fi)
                for bi, name in bones:
                    out[(i, name)] = arm.pose.bones[name].matrix.copy()
            return out

        def maxdiff(a, b):
            return max(max(abs(a[k][r][c] - b[k][r][c]) for r in range(4) for c in range(4)) for k in a)

        def native_err():
            m = 0.0
            for i in idxs:
                x = times[i] * fps
                fi = math.floor(x)
                sc.frame_set(fi, subframe=x - fi)
                for bi, name in bones:
                    o = (i * nb + bi) * 7
                    exp = arm.matrix_world @ mm.pmx_pos_to_blender(arr[o:o + 3], scale)
                    got = arm.matrix_world @ arm.pose.bones[name].matrix.translation
                    m = max(m, (got - exp).length)
            return m

        def muted_now():
            return sorted((fc.data_path, fc.array_index) for fc in sb.action_fcurves(act, arm) if fc.mute)

        def keys_now():
            return sorted((fc.data_path, fc.array_index, len(fc.keyframe_points))
                          for fc in sb.action_fcurves(act, arm))

        before = sample()
        # 前提: ベイク前は原アクションが評価される → native とは違う (でなければ検証にならない)
        res["pre_bake_native_err"] = native_err()

        msgs = []
        r = sb.bake(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        res["messages"] = msgs
        check("overridden_reported", len(r["overridden_bones"]) == len(bones), len(r["overridden_bones"]))
        check("no_override_warning", not any(lv == "WARNING" and "上書き" in m for lv, m in msgs), msgs)
        check("info_muted", any(lv == "INFO" and str(len(tf)) in m for lv, m in msgs), msgs)
        check("muted_count", r["muted_curves"] == len(tf) == len(muted_now()), [r["muted_curves"], len(tf)])
        check("keys_unchanged_after_bake", keys_now() == keys0)
        e = native_err()
        check("bake_visible", e < POS_TOL, e)

        # 再ベイク 2 回: mute 件数は変わらず、効いたまま
        for n in (1, 2):
            r = sb.bake(bpy.context, arm, glb, lambda lv, m: None)
            check(f"rebake{n}_muted", r["muted_curves"] == len(tf) == len(muted_now()), r["muted_curves"])
        check("rebake_visible", native_err() < POS_TOL)
        check("rebake_keys_unchanged", keys_now() == keys0)

        # 利用者がアクティブなアクションを変えた後でも remove_bake は元アクションを戻す
        other = bpy.data.actions.new("Other")
        arm.animation_data.action = other
        check("remove", sb.remove_bake(arm) is True)
        check("unmuted_other_action", muted_now() == [])
        arm.animation_data.action = act
        bpy.data.actions.remove(other)
        d = maxdiff(before, sample())
        check("restored_pose", d < RESTORE_TOL, d)
        check("keys_unchanged_after_remove", keys_now() == keys0)
        check("remove_again_false", sb.remove_bake(arm) is False)

        # 通常の remove (アクション変更なし) も
        sb.bake(bpy.context, arm, glb, lambda lv, m: None)
        check("muted_again", len(muted_now()) == len(tf))
        sb.remove_bake(arm)
        check("unmuted_plain", muted_now() == [] and maxdiff(before, sample()) < RESTORE_TOL)

        # 記録に無い mute (利用者が自分で mute) は戻さない
        user_fc = tf[0]
        user_fc.mute = True
        sb.bake(bpy.context, arm, glb, lambda lv, m: None)
        check("user_mute_not_counted", len(muted_now()) == len(tf))
        sb.remove_bake(arm)
        check("user_mute_kept", user_fc.mute and len(muted_now()) == 1)
        user_fc.mute = False
    except Exception:
        ok = False
        res["exception"] = traceback.format_exc()
    print(json.dumps({"ok": ok, "results": res}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


main()
