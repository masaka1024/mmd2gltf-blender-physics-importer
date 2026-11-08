# ===========================================================================
# selfphys_bake (自作エンジンのベイク → NLA) のヘッドレス検証 (検証の層 5)。
#   blender -b -P tests/headless_selfphys_bake.py -- [model.glb]
# 終了コード 0 = OK。結果は標準出力に JSON 1 行で出す。
# ===========================================================================
import json
import math
import os
import sys
import time
import traceback

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

GLB_ENV = "MMD_TEST_GLB_SMALL"   # 引数を省いたときに使う環境変数 (native/tests/local_env.example.sh)


def _glb_from_args(argv):
    """引数 [glb] → 無ければ環境変数 GLB_ENV。どちらも無ければメッセージを出して終了 (2)。"""
    if argv:
        return os.path.abspath(argv[0])
    v = os.environ.get(GLB_ENV)
    if not v:
        print(json.dumps({"ok": False, "error": "テスト GLB がありません: 引数か環境変数 %s で渡してください" % GLB_ENV},
                         ensure_ascii=False))
        sys.exit(2)
    return os.path.abspath(v)
POS_TOL = 1e-4
ROT_TOL = 1e-3
KEY_TOL = 1e-4


def _fcurves(arm, action):
    """アクションの (このアーマチュア向けスロットの) fcurve 一覧。"""
    from mmd2gltf_blender_importer import selfphys_bake as sb
    return list(sb.action_fcurves(action, arm))


def _bone_of(dp):
    return dp.split('"')[1] if '"' in dp else None


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    glb = _glb_from_args(argv)
    res = {}
    ok = True

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
        sc.render.fps, sc.render.fps_base = 24, 1.0   # 読み込み時と同じ fps

        from mmd2gltf_blender_importer import selfphys_bake as sb
        from mmd2gltf_blender_importer import engine_bridge as eb
        from mmd2gltf_blender_importer import glb_reader, physics_builder, mmd_math as mm

        orig_action = arm.animation_data.action
        orig_fcs = _fcurves(arm, orig_action)
        orig_keys = sum(len(fc.keyframe_points) for fc in orig_fcs)
        orig_tracks = [t.name for t in arm.animation_data.nla_tracks]
        check("no_bake_initially", not sb.has_bake(arm))

        msgs = []
        t0 = time.time()
        r = sb.bake(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        res["bake_seconds"] = round(time.time() - t0, 2)
        res["bake_result"] = {k: (v if k != "skipped_bones" else v[:10]) for k, v in r.items()}
        res["messages"] = msgs[:10]

        model = glb_reader.load(glb)
        scale = model.unit_scale
        nm = eb.NativeModel(glb)
        info = nm.info
        nb, nf = info["bone_count"], info["frame_count"]
        phys = nm.physics_bones
        rest = nm.rest_world()
        times = nm.times()
        arr, warn = nm.bake()
        nm.close()

        check("result_counts", r["frames"] == nf and r["bones"] == len(phys) and r["skipped_bones"] == []
              and r["nan_bone"] is None and r.get("overridden_bones") == [],
              {"frames": r["frames"], "bones": r["bones"], "nf": nf, "phys": len(phys)})
        check("has_bake", sb.has_bake(arm))
        ad = arm.animation_data
        tracks = [t for t in ad.nla_tracks if t.name == sb.BAKE_TRACK_NAME]
        check("one_track", len(tracks) == 1, len(tracks))
        check("track_on_top", ad.nla_tracks[-1].name == sb.BAKE_TRACK_NAME,
              [t.name for t in ad.nla_tracks])
        strip = tracks[0].strips[0] if tracks and len(tracks[0].strips) == 1 else None
        check("strip", strip is not None and strip.action is not None
              and strip.action.name == sb.BAKE_ACTION_NAME and strip.blend_type == "REPLACE",
              None if strip is None else [strip.action.name if strip.action else None, strip.blend_type,
                                          strip.frame_start, strip.frame_end])
        check("orig_action_kept", ad.action == orig_action
              and sum(len(fc.keyframe_points) for fc in _fcurves(arm, orig_action)) == orig_keys,
              orig_keys)

        # --- 層 5: 評価後のボーン位置・回転 vs native 出力 ------------------
        fps = sc.render.fps / sc.render.fps_base
        bones = []
        for bi in phys:
            bone = physics_builder.find_bone(arm, model, bi, scale)
            bones.append((bi, bone.name))
        idxs = sorted(set(list(range(0, nf, 250)) + [0, 1, 2, nf - 3, nf - 2, nf - 1]))
        max_pos, max_rot, worst = 0.0, 0.0, None
        mw = arm.matrix_world
        for i in idxs:
            x = times[i] * fps
            fi = math.floor(x)
            sc.frame_set(fi, subframe=x - fi)
            for bi, name in bones:
                o = (i * nb + bi) * 7
                w7 = arr[o:o + 7]
                exp_pos = mw @ mm.pmx_pos_to_blender(w7[:3], scale)
                pb = arm.pose.bones[name]
                got = mw @ pb.matrix
                d = (got.translation - exp_pos).length
                exp_rot = (mw @ sb.pmx_world_to_pose_matrix(w7, rest[bi], arm.data.bones[name], scale)).to_quaternion()
                # 角度 = 2·asin(|差分クォータニオンのベクトル部|) (符号 q/-q に依らず、acos より精度が良い)
                dq = got.to_quaternion().normalized().rotation_difference(exp_rot.normalized())
                a = 2.0 * math.asin(min(1.0, math.sqrt(dq.x * dq.x + dq.y * dq.y + dq.z * dq.z)))
                if d > max_pos:
                    max_pos = d
                if a > max_rot:
                    max_rot = a
                if (d > POS_TOL or a > ROT_TOL) and worst is None:
                    worst = {"frame_index": i, "bone": name, "pos_err": d, "rot_err": a}
        # 独立な回転検査: 揺れ物でないボーンは glTF インポーター自身のアクションで動く
        # (ベイクのコードと無関係)。その評価後の回転と、同じボーンの native 出力に
        # pmx_world_to_pose_matrix を当てた回転を比べる。式は全ボーン共通なので、
        # 末端の揺れ物ボーンの回転式もこれで検証される。
        # 揺れ物ボーンの子孫 (スカート末端など、剛体の無い先端ボーン) は native 出力に物理が
        # 伝播しないので対象外にし、別途 (合否に使わず) 誤差だけ出す。
        phys_set = set(phys)
        phys_names = {physics_builder.find_bone(arm, model, bi, scale).name for bi in phys}

        def under_phys(bone):
            p = bone.parent
            while p is not None:
                if p.name in phys_names:
                    return True
                p = p.parent
            return False
        drv_bones, tip_bones, seen = [], [], set()
        for bi in range(nb):
            if bi in phys_set:
                continue
            bone = physics_builder.find_bone(arm, model, bi, scale)
            if bone is None or bone.name in seen:
                continue
            seen.add(bone.name)
            (tip_bones if under_phys(bone) else drv_bones).append((bi, bone.name))
        max_tip = 0.0
        max_drot, dworst = 0.0, None
        for i in idxs:
            x = times[i] * fps
            fi = math.floor(x)
            sc.frame_set(fi, subframe=x - fi)
            for bi, name in drv_bones + tip_bones:
                o = (i * nb + bi) * 7
                w7 = arr[o:o + 7]
                got = (mw @ arm.pose.bones[name].matrix).to_quaternion().normalized()
                exp_rot = (mw @ sb.pmx_world_to_pose_matrix(w7, rest[bi], arm.data.bones[name], scale)
                           ).to_quaternion().normalized()
                dq = got.rotation_difference(exp_rot)
                a = 2.0 * math.asin(min(1.0, math.sqrt(dq.x * dq.x + dq.y * dq.y + dq.z * dq.z)))
                if (bi, name) in tip_bones:
                    max_tip = max(max_tip, a)
                    continue
                if a > max_drot:
                    max_drot = a
                    dworst = {"frame_index": i, "bone": name, "rot_err": a}
        check("driven_rot_independent", len(drv_bones) > 0 and max_drot < ROT_TOL,
              {"max": max_drot, "bones": len(drv_bones), "worst": dworst})
        res["info_tips_under_physics"] = {"bones": len(tip_bones), "max_rot_err": max_tip,
                                          "has_physics_descendant": any(
                                              any(c.name in phys_names for c in arm.data.bones[n].children_recursive)
                                              for _, n in tip_bones)}

        # 静止姿勢 (world7 == rest7) → matrix_basis の回転は単位
        from mathutils import Matrix
        max_id = 0.0
        all_map = {}
        for bi in range(nb):
            bone = physics_builder.find_bone(arm, model, bi, scale)
            if bone is not None:
                all_map.setdefault(bone.name, bi)
        for name, bi in all_map.items():
            bone = arm.data.bones[name]
            pose = sb.pmx_world_to_pose_matrix(rest[bi], rest[bi], bone, scale)
            par = bone.parent
            if par is not None and par.name in all_map:
                pp = sb.pmx_world_to_pose_matrix(rest[all_map[par.name]], rest[all_map[par.name]], par, scale)
                basis = bone.matrix_local.inverted() @ par.matrix_local @ pp.inverted() @ pose
            else:
                basis = bone.matrix_local.inverted() @ pose
            err = max(abs(basis.to_3x3()[r][c] - (1.0 if r == c else 0.0)) for r in range(3) for c in range(3))
            max_id = max(max_id, err)
        check("rest_pose_identity", max_id < 1e-5, {"max": max_id, "bones": len(all_map)})

        check("layer5_pos", max_pos < POS_TOL, {"max": max_pos, "frames": len(idxs), "bones": len(bones)})
        check("layer5_rot", max_rot < ROT_TOL, {"max": max_rot, "worst": worst})

        # --- キー位置: 元アクション (glTF インポーター) と同じ時刻→フレーム対応 ---
        phys_names = {n for _, n in bones}
        drv = None
        for fc in orig_fcs:
            bn = _bone_of(fc.data_path)
            if bn and bn not in phys_names and len(fc.keyframe_points) == nf:
                drv = fc
                break
        bake_fcs = _fcurves(arm, bpy.data.actions[sb.BAKE_ACTION_NAME])
        bfc = bake_fcs[0]
        if drv is not None:
            ox = [kp.co[0] for kp in drv.keyframe_points]
            bx = [kp.co[0] for kp in bfc.keyframe_points]
            dmax = max(abs(a - b) for a, b in zip(ox, bx)) if len(ox) == len(bx) else None
            check("key_frames_match_importer", dmax is not None and dmax < KEY_TOL,
                  {"driven": drv.data_path, "baked": bfc.data_path, "max_diff": dmax,
                   "n": [len(ox), len(bx)]})
        else:
            check("key_frames_match_importer", False, "no driven fcurve with nf keys")
        check("bake_fcurve_count", len(bake_fcs) == 7 * len(phys), len(bake_fcs))
        check("interp_linear", all(kp.interpolation == "LINEAR" for kp in bfc.keyframe_points[:50]))

        # --- 焼き直し (fps 違い): トラック 1 本、キー = t * fps / fps_base -----
        sc.render.fps, sc.render.fps_base = 30, 1.001
        t0 = time.time()
        r2 = sb.bake(bpy.context, arm, glb, lambda lv, m: None)
        res["rebake_seconds"] = round(time.time() - t0, 2)
        tracks = [t for t in ad.nla_tracks if t.name == sb.BAKE_TRACK_NAME]
        check("rebake_one_track", len(tracks) == 1 and len([a for a in bpy.data.actions
                                                             if a.name.startswith(sb.BAKE_ACTION_NAME)]) == 1,
              [len(tracks), [a.name for a in bpy.data.actions]])
        bfc = _fcurves(arm, bpy.data.actions[sb.BAKE_ACTION_NAME])[0]
        f2 = sc.render.fps / sc.render.fps_base   # fps_base は float32 で保存される
        # キーの co は float32 なので、期待値も float32 に丸めて比べる (7000 フレーム付近の ulp は 5e-4)
        import numpy as np
        dmax = max(abs(kp.co[0] - float(np.float32(times[k] * f2))) for k, kp in enumerate(bfc.keyframe_points))
        check("rebake_fps_keys", r2["frames"] == nf and dmax < KEY_TOL, dmax)
        check("rebake_strip_range", tracks[0].strips[0].frame_end >= times[-1] * f2 - 1e-3,
              [tracks[0].strips[0].frame_start, tracks[0].strips[0].frame_end])
        check("orig_tracks_kept", [t.name for t in ad.nla_tracks if t.name != sb.BAKE_TRACK_NAME] == orig_tracks,
              [t.name for t in ad.nla_tracks])

        # --- 削除 ---
        check("remove", sb.remove_bake(arm) is True)
        check("removed", not sb.has_bake(arm)
              and sb.BAKE_TRACK_NAME not in [t.name for t in ad.nla_tracks]
              and sb.BAKE_ACTION_NAME not in bpy.data.actions)
        check("remove_again_false", sb.remove_bake(arm) is False)
        check("orig_action_after_remove", ad.action == orig_action
              and sum(len(fc.keyframe_points) for fc in _fcurves(arm, orig_action)) == orig_keys)

        # --- 書き込み途中の失敗: 途中までの状態を残さない --------------------
        orig_write = sb._write_fcurve
        calls = []

        def bad_write(fc, xs, ys):
            calls.append(1)
            if len(calls) > 20:
                raise RuntimeError("test: write failed")
            orig_write(fc, xs, ys)
        sb._write_fcurve = bad_write
        raised = None
        try:
            sb.bake(bpy.context, arm, glb, lambda lv, m: None)
        except RuntimeError as e:
            raised = str(e)
        finally:
            sb._write_fcurve = orig_write
        check("failed_bake_raises", raised == "test: write failed", raised)
        check("failed_bake_cleaned", not sb.has_bake(arm) and sb.BAKE_ACTION_NAME not in bpy.data.actions
              and sb.PROP_MUTED not in arm and ad.action == orig_action,
              [sb.has_bake(arm), [a.name for a in bpy.data.actions], sb.PROP_MUTED in arm])

        # --- 発散: nan_bone はボーン名だけ、メッセージは 1 回だけ包む ----------
        orig_bake = eb.NativeModel.bake

        def nan_bake(self, progress=None):
            a, _w = orig_bake(self, progress)
            w = "physics diverged (NaN) first at bone: テスト骨"
            self.last_nan_bone = eb.nan_bone_from_message(w)
            return a, w
        eb.NativeModel.bake = nan_bake
        msgs = []
        try:
            r3 = sb.bake(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        finally:
            eb.NativeModel.bake = orig_bake
        check("nan_bone_name", r3["nan_bone"] == "テスト骨", r3["nan_bone"])
        check("nan_message", ("WARNING", "物理が発散しました (最初の検出: ボーン 'テスト骨')") in msgs, msgs)
        sb.remove_bake(arm)

        # --- Bullet の配線が残っていると WARNING (ベイク・ライブとも) --------
        from mmd2gltf_blender_importer import selfphys_live as live
        physics_builder.build(bpy.context, arm, model, physics_builder.PhysicsOptions(), lambda lv, m: None)
        wired = physics_builder.bullet_constrained_bones(arm)
        check("wired_constraints", len(wired) > 0 and physics_builder.has_bullet_wiring(arm), len(wired))
        msgs = []
        sb.bake(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        check("wired_bake_warning", any(lv == "WARNING" and "物理を削除" in m for lv, m in msgs), msgs)
        sb.remove_bake(arm)
        msgs = []
        live.start(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        live.stop()
        check("wired_live_warning", any(lv == "WARNING" and "物理を削除" in m for lv, m in msgs), msgs)
        physics_builder.remove_existing(arm)
        check("unwired", not physics_builder.has_bullet_wiring(arm))
        msgs = []
        sb.bake(bpy.context, arm, glb, lambda lv, m: msgs.append((lv, m)))
        check("unwired_no_warning", not any("物理を削除" in m for _, m in msgs), msgs)
        sb.remove_bake(arm)
    except Exception:
        ok = False
        res["exception"] = traceback.format_exc()

    print(json.dumps({"ok": ok, "results": res}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


main()
