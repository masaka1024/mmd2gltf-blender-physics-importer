# ===========================================================================
# selfphys_live (自作エンジンのリアルタイム表示) のヘッドレス検証 (検証の層 7)。
#   blender -b -P tests/headless_selfphys_live.py -- [model.glb]
# 終了コード 0 = OK。結果は標準出力に JSON 1 行で出す。
# ベイクとの差は記録のみ (合否に使わない)。
# ===========================================================================
import json
import math
import os
import sys
import time
import traceback
from array import array

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DEFAULT_GLB = os.environ.get("MMD_TEST_GLB_SMALL") or "/Users/masaka1024/Desktop/IA_Conqueror_v4のコピー.glb"
ROUNDTRIP_TOL = 1e-5
FRAMES = list(range(1, 61))


def _quat_angle(a, b):
    dq = a.normalized().rotation_difference(b.normalized())
    return 2.0 * math.asin(min(1.0, math.sqrt(dq.x * dq.x + dq.y * dq.y + dq.z * dq.z)))


def _written_vs_native(arm, live, sb, phys_pairs, rest, scale):
    """直近の live_step 出力と評価済み揺れ物ボーン行列の差 (位置, 回転)。Bone はその場で引く。"""
    out = live._last_out
    wp, wr = 0.0, 0.0
    for bi, name in phys_pairs:
        exp = sb.pmx_world_to_pose_matrix(out[bi * 7:bi * 7 + 7], rest[bi], arm.data.bones[name], scale)
        got = arm.pose.bones[name].matrix
        wp = max(wp, (got.translation - exp.translation).length)
        wr = max(wr, _quat_angle(got.to_quaternion(), exp.to_quaternion()))
    return wp, wr


def _has_nan(arm, names):
    return [n for n in names
            if any(not math.isfinite(arm.pose.bones[n].matrix[r][c]) for r in range(4) for c in range(4))]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    glb = os.path.abspath(argv[0]) if argv else DEFAULT_GLB
    res = {}
    ok = True

    def check(name, cond, detail=None):
        nonlocal ok
        res[name] = {"ok": bool(cond), "detail": detail}
        ok = ok and bool(cond)

    live = None
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=glb, bone_heuristic="TEMPERANCE")
        arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        arm["mmd2gltf_glb"] = glb
        sc = bpy.context.scene
        sc.render.fps, sc.render.fps_base = 24, 1.0

        from mmd2gltf_blender_importer import selfphys_live as live
        from mmd2gltf_blender_importer import selfphys_bake as sb
        from mmd2gltf_blender_importer import engine_bridge as eb, glb_reader

        model = glb_reader.load(glb)
        scale = model.unit_scale
        nm = eb.NativeModel(glb)
        nb = nm.info["bone_count"]
        phys = nm.physics_bones
        rest = nm.rest_world()
        nm.close()
        pmx_bone, _ = sb.bone_map(arm, model, nb, scale)
        phys_names = [pmx_bone[bi].name for bi in phys if bi in pmx_bone]
        phys_pairs = [(bi, pmx_bone[bi].name) for bi in phys if bi in pmx_bone]

        # --- 逆変換: blender_pose_to_pmx_world(pmx_world_to_pose_matrix(x)) ≈ x -----
        from mathutils import Quaternion, Vector
        max_p, max_r = 0.0, 0.0
        for bi, bone in pmx_bone.items():
            x = rest[bi]
            m = sb.pmx_world_to_pose_matrix(x, rest[bi], bone, scale)
            y = live.blender_pose_to_pmx_world(m, rest[bi], bone, scale)
            max_p = max(max_p, (Vector(y[:3]) - Vector(x[:3])).length)
            max_r = max(max_r, _quat_angle(Quaternion((y[6], y[3], y[4], y[5])),
                                           Quaternion((x[6], x[3], x[4], x[5]))))
        check("roundtrip_rest", len(pmx_bone) > 0 and max_p < ROUNDTRIP_TOL and max_r < ROUNDTRIP_TOL,
              {"bones": len(pmx_bone), "max_pos": max_p, "max_rot": max_r})
        # 静止姿勢以外 (任意の回転) でも往復する
        max_p2, max_r2 = 0.0, 0.0
        for k, (bi, bone) in enumerate(list(pmx_bone.items())[:40]):
            q = Quaternion((0.3 + 0.01 * k, 0.5, -0.2, 0.7)).normalized()
            x = (rest[bi][0] + 0.1, rest[bi][1] - 0.2, rest[bi][2] + 0.3, q.x, q.y, q.z, q.w)
            m = sb.pmx_world_to_pose_matrix(x, rest[bi], bone, scale)
            y = live.blender_pose_to_pmx_world(m, rest[bi], bone, scale)
            max_p2 = max(max_p2, (Vector(y[:3]) - Vector(x[:3])).length)
            max_r2 = max(max_r2, _quat_angle(Quaternion((y[6], y[3], y[4], y[5])), q))
        check("roundtrip_rotated", max_p2 < ROUNDTRIP_TOL and max_r2 < ROUNDTRIP_TOL,
              {"max_pos": max_p2, "max_rot": max_r2})

        # --- 再生 1..60 ---------------------------------------------------
        check("not_running_initially", not live.is_running())
        sc.frame_set(1)
        live.start(bpy.context, arm, glb)
        check("running_after_start", live.is_running())
        live._stats.update({"resets": 0, "steps": 0, "handler_seconds": 0.0, "handler_calls": 0})
        live_pos = {}
        frame_ms = []
        nan = []
        wp, wr = 0.0, 0.0
        for f in FRAMES:
            t0 = time.perf_counter()
            sc.frame_set(f)
            frame_ms.append((time.perf_counter() - t0) * 1000.0)
            live_pos[f] = {n: arm.pose.bones[n].matrix.translation.copy() for n in phys_names}
            if f > 1:
                # 書いたポーズが同じフレームで native の出力どおりに評価されている (遅れ 0)
                a, b = _written_vs_native(arm, live, sb, phys_pairs, rest, scale)
                wp, wr = max(wp, a), max(wr, b)
            for n in phys_names:
                m = arm.pose.bones[n].matrix
                if any(not math.isfinite(m[r][c]) for r in range(4) for c in range(4)):
                    nan.append((f, n))
        st = dict(live._stats)
        hs = st["handler_seconds"] / max(1, st["handler_calls"]) * 1000.0
        res["timing_ms"] = {"frame_set_mean": sum(frame_ms) / len(frame_ms), "frame_set_max": max(frame_ms),
                            "handler_mean": hs, "handler_calls": st["handler_calls"]}
        check("still_running", live.is_running() and live.last_error() is None, live.last_error())
        check("no_nan", not nan, nan[:5])
        check("written_matches_native", wp < 1e-4 and wr < 1e-3, {"max_pos": wp, "max_rot": wr})
        check("stats_1_60", st["resets"] == 1 and st["steps"] == len(FRAMES) - 1, st)
        moved = []
        for n in phys_names:
            mb = arm.pose.bones[n].matrix_basis
            ang = mb.to_quaternion().angle
            if ang > 1e-3 or mb.translation.length > 1e-4:
                moved.append((n, ang))
        check("bones_moved", len(moved) >= 1, {"moved": len(moved), "of": len(phys_names),
                                              "sample": moved[:3]})
        moved_from_start = max((live_pos[60][n] - live_pos[1][n]).length for n in phys_names)
        res["info_max_move_1_to_60"] = moved_from_start

        # --- 30 → 10 でリセット --------------------------------------------
        sc.frame_set(30)
        r0, s0 = live._stats["resets"], live._stats["steps"]
        sc.frame_set(10)
        check("jump_back_resets", live._stats["resets"] == r0 + 1 and live._stats["steps"] == s0,
              {"before": [r0, s0], "after": [live._stats["resets"], live._stats["steps"]]})
        sc.frame_set(11)
        check("step_after_jump", live._stats["steps"] == s0 + 1, live._stats["steps"])

        # --- ベイク済みでは計算しない -------------------------------------
        live.stop()
        check("stopped", not live.is_running())
        live.stop()   # 2 回目も安全
        r = sb.bake(bpy.context, arm, glb, lambda lv, m: None)
        check("baked", sb.has_bake(arm) and r["bones"] == len(phys), r.get("bones"))
        bake_mat = {}
        for f in FRAMES:
            sc.frame_set(f)
            bake_mat[f] = {n: arm.pose.bones[n].matrix.copy() for n in phys_names}
        live.start(bpy.context, arm, glb)
        s_before = dict(live._stats)
        max_d = 0.0
        for f in FRAMES:
            sc.frame_set(f)
            for n in phys_names:
                d = arm.pose.bones[n].matrix - bake_mat[f][n]
                max_d = max(max_d, max(abs(d[r][c]) for r in range(4) for c in range(4)))
        check("baked_no_compute", live._stats["steps"] == s_before["steps"]
              and live._stats["resets"] == s_before["resets"] and max_d < 1e-6,
              {"max_matrix_diff": max_d, "stats": [s_before["steps"], live._stats["steps"]]})

        # ベイクとの差 (記録のみ): 同じフレーム 1..60 のアーマチュア空間のボーン位置
        diff_max, diff_at = 0.0, None
        per_frame = []
        for f in FRAMES:
            fm = 0.0
            for n in phys_names:
                d = (live_pos[f][n] - bake_mat[f][n].translation).length
                fm = max(fm, d)
                if d > diff_max:
                    diff_max, diff_at = d, (f, n)
            per_frame.append(fm)
        res["info_live_vs_bake"] = {"max_pos_diff": diff_max, "at": diff_at,
                                    "frame1": per_frame[0], "frame30": per_frame[29], "frame60": per_frame[59],
                                    "unit_scale": scale}

        # --- 開き直し ---------------------------------------------------
        sb.remove_bake(arm)
        sc.frame_set(1)
        sc.frame_set(2)
        check("runs_after_remove_bake", live.is_running() and live._stats["steps"] > s_before["steps"],
              live._stats)
        pmx_bone = None   # 以降は編集モードを出入りするので Bone 参照を持たない

        # --- サブフレーム: リセットもステップもしない ----------------------
        st0 = dict(live._stats)
        sc.frame_set(2, subframe=0.5)
        sc.frame_set(2, subframe=0.25)
        st1 = dict(live._stats)
        sc.frame_set(3)
        check("subframe_no_reset", st1["resets"] == st0["resets"] and st1["steps"] == st0["steps"]
              and live._stats["steps"] == st0["steps"] + 1 and live._stats["resets"] == st0["resets"],
              {"before": [st0["resets"], st0["steps"]], "sub": [st1["resets"], st1["steps"]],
               "after": [live._stats["resets"], live._stats["steps"]]})

        # --- 編集モードの出入り ------------------------------------------
        vl = bpy.context.view_layer
        vl.objects.active = arm
        arm.select_set(True)
        rb0 = live._stats["rebuilds"]
        bpy.ops.object.mode_set(mode="EDIT")
        sc.frame_set(4)   # 編集中は何もしない
        bpy.ops.object.mode_set(mode="OBJECT")
        sc.frame_set(5)   # リセット
        sc.frame_set(6)   # ステップ
        sc.frame_set(7)
        wp, wr = _written_vs_native(arm, live, sb, phys_pairs, rest, scale)
        check("edit_mode_toggle", live.is_running() and live.last_error() is None
              and not _has_nan(arm, phys_names) and wp < 1e-4 and wr < 1e-3,
              {"running": live.is_running(), "error": live.last_error(), "rebuilds": live._stats["rebuilds"] - rb0,
               "max_pos": wp, "max_rot": wr})
        res["info_edit_mode_rebuilds"] = live._stats["rebuilds"] - rb0

        # --- 改名 -------------------------------------------------------
        arm.name = "ArmRenamedForLiveTest"
        s0 = live._stats["steps"]
        sc.frame_set(8)
        check("rename_keeps_running", live.is_running() and live._stats["steps"] == s0 + 1
              and live._arm_name == "ArmRenamedForLiveTest", {"steps": [s0, live._stats["steps"]],
                                                               "name": live._arm_name})

        # --- start() の失敗を呼び出し側に伝える --------------------------
        orig_reset = eb.NativeModel.live_reset

        def bad_reset(self, w):
            raise eb.NativeError("test: reset failed")
        eb.NativeModel.live_reset = bad_reset
        raised = None
        try:
            live.start(bpy.context, arm, glb)
        except Exception as e:
            raised = "%s: %s" % (type(e).__name__, e)
        finally:
            eb.NativeModel.live_reset = orig_reset
        check("start_failure_raises", raised is not None and "reset failed" in raised and not live.is_running(),
              raised)

        # --- 発散の警告: native が最初のボーン名を返す ---------------------
        nm2 = eb.NativeModel(glb)
        w = array("f", [v for r7 in rest for v in r7])
        nm2.live_reset(w)
        bad = array("f", [float("nan")] * len(w))
        nm2.live_step(bad, 1.0 / 24)
        warn = nm2.live_warning
        nm2.close()
        check("nan_warning_has_bone", warn is not None and "first at bone:" in warn, warn)

        # --- 削除 -------------------------------------------------------
        live.start(bpy.context, arm, glb)
        sc.frame_set(9)
        bpy.data.objects.remove(arm, do_unlink=True)
        arm = None
        sc.frame_set(10)
        check("delete_stops", not live.is_running() and live.last_error() is not None, live.last_error())
        bpy.ops.import_scene.gltf(filepath=glb, bone_heuristic="TEMPERANCE")
        arm2 = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
        live.start(bpy.context, arm2, glb)
        sc.frame_set(1)
        sc.frame_set(2)
        check("running_before_reopen", live.is_running())
        bpy.ops.wm.read_factory_settings(use_empty=True)
        check("reopen_stops", not live.is_running())
        bpy.context.scene.frame_set(5)
        bpy.context.scene.frame_set(6)
        check("reopen_no_error", live.last_error() is None and not live.is_running(), live.last_error())
    except Exception:
        ok = False
        res["exception"] = traceback.format_exc()
    finally:
        if live is not None:
            try:
                live.stop()
            except Exception:
                pass

    print(json.dumps({"ok": ok, "results": res}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


main()
