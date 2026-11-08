# ===========================================================================
# engine_bridge (ctypes 窓口) のヘッドレス検証。
#   blender -b -P tests/headless_bridge.py -- [model.glb]
# 終了コード 0 = OK。結果は標準出力に JSON 1 行で出す。
# ===========================================================================
import json
import os
import sys
import traceback

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
        from mmd2gltf_blender_importer import engine_bridge as eb

        # 通常
        eb._reset_for_tests()
        st = eb.load_status()
        check("load_ok", st[0] is True, list(st))
        m = eb.NativeModel(glb)
        info = m.info
        check("bone_count", info["bone_count"] == 179, info)
        check("physics_bones", len(m.physics_bones) == info["physics_bone_count"], len(m.physics_bones))
        rw = m.rest_world()
        check("rest_world", len(rw) == 179 and len(rw[0]) == 7, len(rw))
        check("times", len(m.times()) == info["frame_count"], info["frame_count"])
        # live
        from array import array
        w = array("f", [v for t in rw for v in t])
        m.live_reset(w)
        out = m.live_step(w, 1.0 / 30.0)
        check("live_step", len(out) == 179 * 7, len(out))
        # 短い bake は重いので進捗コールバックだけ確認 (全フレーム)
        calls = []
        arr, warn = m.bake(progress=lambda d, t: calls.append((d, t)))
        check("bake", len(arr) == info["frame_count"] * 179 * 7 and len(calls) > 0, [len(arr), warn, len(calls)])
        # 進捗コールバックの例外は bake 完了後に同じ型で投げ直される
        n_calls = []

        def bad_cb(d, t):
            n_calls.append(d)
            raise KeyError("boom")
        try:
            m.bake(progress=bad_cb)
            check("progress_exc", False, "no raise")
        except KeyError:
            check("progress_exc", len(n_calls) == 1, len(n_calls))
        m.close()
        m.close()
        check("close_twice", True)
        try:
            m.info
            check("closed_raises", False)
        except eb.NativeError:
            check("closed_raises", True)

        # 存在しないパス
        bad = os.path.join(REPO, "no_such_dir", "libmmdphys.dylib")
        os.environ["MMD_NATIVE_LIB"] = bad
        eb._reset_for_tests()
        st = eb.load_status()
        check("missing_lib", st == (False, "ライブラリが見つかりません: " + bad), list(st))
        try:
            eb.NativeModel(glb)
            check("missing_lib_raises", False)
        except eb.NativeError:
            check("missing_lib_raises", True)
        del os.environ["MMD_NATIVE_LIB"]

        # Linux など未同梱のプラットフォーム: .so を探し、無ければ「見つかりません」(→ Bullet 方式)
        orig_plat = eb.sys.platform
        try:
            eb.sys.platform = "linux"
            check("linux_lib_name", eb._lib_path().endswith(os.path.join("native_lib", "libmmdphys.so")),
                  eb._lib_path())
        finally:
            eb.sys.platform = orig_plat

        # 発散メッセージ → ボーン名だけ
        check("nan_bone_parse", eb.nan_bone_from_message(
            "physics diverged (NaN) first at bone: 左髪1; previous pose kept for affected bones") == "左髪1"
              and eb.nan_bone_from_message("physics diverged (NaN) first at bone: 右袖") == "右袖"
              and eb.nan_bone_from_message("other") is None and eb.nan_bone_from_message(None) is None)

        # 版数違い
        eb._reset_for_tests()
        eb.EXPECTED_API_VERSION = 2
        st = eb.load_status()
        check("version_mismatch", st[0] is False and "版数が違います" in st[1], list(st))
        eb.EXPECTED_API_VERSION = 1
        eb._reset_for_tests()
        check("recover", eb.load_status()[0] is True)
    except Exception:
        ok = False
        res["exception"] = traceback.format_exc()

    print(json.dumps({"ok": ok, "results": res}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


main()
