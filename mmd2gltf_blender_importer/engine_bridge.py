# ===========================================================================
# ネイティブ物理エンジン (libmmdphys / mmdphys.dll) を ctypes で呼ぶ窓口。
#   - ライブラリの場所: 環境変数 MMD_NATIVE_LIB (ファイルのフルパス) → このモジュールの隣の native_lib/
#   - load_status() で「読み込めたか・できない理由」を返す (結果はキャッシュ。アドオンの
#     unregister() で reset_status() を呼ぶので、無効化・有効化で読み直せる)。
#   - ライブラリが無い環境 (Linux / Intel Mac など未同梱のプラットフォーム) では
#     「ライブラリが見つかりません: …」を理由に返し、アドオンは Blender Bullet 方式に切り替わる。
#   - 姿勢 = 1 ボーン 7 float (px,py,pz,qx,qy,qz,qw)、raw PMX 空間。
#   - 戻り値の約束は native/include/mmdphys_capi.h を参照。
# ===========================================================================
import ctypes
import os
import sys
from array import array

EXPECTED_API_VERSION = 1

MMD_OK = 0
MMD_WARN_NAN = 1
MMD_ERR = -1


class NativeError(Exception):
    pass


class _MmdInfo(ctypes.Structure):
    _fields_ = [("bone_count", ctypes.c_int32), ("physics_bone_count", ctypes.c_int32),
                ("frame_count", ctypes.c_int32), ("unit_scale", ctypes.c_float)]


_PROGRESS_FN = ctypes.CFUNCTYPE(None, ctypes.c_int32, ctypes.c_int32, ctypes.c_void_p)

_lib = None
_status = None  # (bool, str) のキャッシュ


def _lib_path():
    env = os.environ.get("MMD_NATIVE_LIB")
    if env:
        return env
    if sys.platform == "win32":
        name = "mmdphys.dll"
    elif sys.platform == "darwin":
        name = "libmmdphys.dylib"
    else:
        name = "libmmdphys.so"   # 現在は同梱していない (見つからなければ Bullet 方式)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "native_lib", name)


def _is_arch_error(e):
    s = str(e).lower()
    return ("incompatible architecture" in s or "mach-o" in s or "wrong architecture" in s
            or "not a valid win32 application" in s or "winerror 193" in s
            or "wrong elf class" in s or "%1" in s)


def _bind(lib):
    c = ctypes
    vp, i32, i64, f32, cp = c.c_void_p, c.c_int32, c.c_int64, c.c_float, c.c_char_p
    pf, pi = c.POINTER(c.c_float), c.POINTER(c.c_int32)

    def sig(name, res, args):
        f = getattr(lib, name)
        f.restype = res
        f.argtypes = args

    sig("mmd_api_version", i32, [])
    sig("mmd_last_error", cp, [])
    sig("mmd_create", vp, [cp])
    sig("mmd_destroy", None, [vp])
    sig("mmd_get_info", i32, [vp, c.POINTER(_MmdInfo)])
    sig("mmd_get_physics_bones", i32, [vp, pi, i32])
    sig("mmd_get_rest_world", i32, [vp, pf, i32])
    sig("mmd_get_times", i32, [vp, pf, i32])
    sig("mmd_bake", i32, [vp, pf, i64, _PROGRESS_FN, vp])
    sig("mmd_live_reset", i32, [vp, pf])
    sig("mmd_live_step", i32, [vp, pf, f32, pf])


def _load():
    """(lib | None, 理由) を返す。"""
    path = _lib_path()
    if not os.path.isfile(path):
        return None, "ライブラリが見つかりません: " + path
    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        if _is_arch_error(e):
            return None, "CPU の種類が違います"
        return None, "ライブラリを読み込めません: %s" % e
    # 版数を先に見る (将来版は関数の構成が違うことがあるため、bind より前)
    try:
        lib.mmd_api_version.restype = ctypes.c_int32
        lib.mmd_api_version.argtypes = []
        ver = lib.mmd_api_version()
    except AttributeError as e:
        return None, "ライブラリを読み込めません: %s" % e
    if ver != EXPECTED_API_VERSION:
        return None, "版数が違います (lib %d / addon %d)" % (ver, EXPECTED_API_VERSION)
    try:
        _bind(lib)
    except AttributeError as e:
        return None, "ライブラリを読み込めません: %s" % e
    return lib, ""


def load_status():
    """(読み込めたか, 理由)。結果はキャッシュされる。読めなかったときは理由の全文を 1 回だけ stderr に出す。"""
    global _lib, _status
    if _status is None:
        _lib, reason = _load()
        _status = (_lib is not None, reason)
        if _lib is None:
            print("mmd2gltf: 自作エンジンを読み込めません (Blender Bullet 方式を使います): " + reason,
                  file=sys.stderr)
    return _status


def reset_status():
    """キャッシュを捨てる (次の load_status() で読み直す)。アドオンの unregister() から呼ぶ。
    読み込み済みの共有ライブラリはプロセスから外せないが、失敗理由の再判定には十分。"""
    global _lib, _status
    _lib = None
    _status = None


_reset_for_tests = reset_status


def nan_bone_from_message(msg):
    """native の発散メッセージ ("... first at bone: <名前>; ..." / "... first at bone: <名前>")
    からボーン名だけを取り出す。見つからなければ None。"""
    if not msg:
        return None
    key = "first at bone: "
    i = msg.find(key)
    if i < 0:
        return None
    name = msg[i + len(key):]
    j = name.find(";")
    if j >= 0:
        name = name[:j]
    return name.strip() or None


def _last_error():
    msg = _lib.mmd_last_error() if _lib is not None else None
    return msg.decode("utf-8", "replace") if msg else ""


def _addr(arr):
    """array('f') の先頭を指す float* (コピー無し)。"""
    n = len(arr)
    if n == 0:
        return None
    return ctypes.cast((ctypes.c_float * n).from_buffer(arr), ctypes.POINTER(ctypes.c_float))


def _check_len(arr, n, what):
    if not isinstance(arr, array) or arr.typecode != "f":
        raise NativeError("%s は array('f') で渡してください" % what)
    if len(arr) != n:
        raise NativeError("%s の長さが違います (%d / 期待 %d)" % (what, len(arr), n))


class NativeModel:
    def __init__(self, path):
        self._h = None
        ok, reason = load_status()
        if not ok:
            raise NativeError(reason)
        h = _lib.mmd_create(os.fspath(path).encode("utf-8"))
        if not h:
            raise NativeError(_last_error() or "モデルを作成できません")
        self._h = h
        self._info = None
        self.live_warning = None
        self.last_nan_bone = None   # 直近の bake / live_step で発散した最初のボーン名 (無ければ None)
        info = self._call_info()
        self._bones = info["bone_count"]
        self._frames = info["frame_count"]

    def _handle(self):
        if self._h is None:
            raise NativeError("モデルは閉じられています")
        return self._h

    def _call_info(self):
        out = _MmdInfo()
        if _lib.mmd_get_info(self._handle(), ctypes.byref(out)) < 0:
            raise NativeError(_last_error())
        return {"bone_count": out.bone_count, "physics_bone_count": out.physics_bone_count,
                "frame_count": out.frame_count, "unit_scale": out.unit_scale}

    @property
    def info(self):
        return self._call_info()

    @property
    def physics_bones(self):
        n = self._call_info()["physics_bone_count"]
        buf = (ctypes.c_int32 * max(n, 1))()
        got = _lib.mmd_get_physics_bones(self._handle(), buf, n)
        if got < 0:
            raise NativeError(_last_error())
        return list(buf[:got])

    def rest_world(self):
        n = self._bones
        arr = array("f", [0.0]) * (7 * n)
        got = _lib.mmd_get_rest_world(self._handle(), _addr(arr), n)
        if got < 0:
            raise NativeError(_last_error())
        return [tuple(arr[i * 7:i * 7 + 7]) for i in range(got)]

    def times(self):
        n = self._frames
        arr = array("f", [0.0]) * n
        got = _lib.mmd_get_times(self._handle(), _addr(arr), n)
        if got < 0:
            raise NativeError(_last_error())
        return list(arr[:got])

    def bake(self, progress=None):
        """全フレームを焼く。戻り値 (array('f') [frame][bone][7], 警告文 | None)。
        発散したら self.last_nan_bone に最初のボーン名を入れる。"""
        h = self._handle()
        n = self._frames * self._bones * 7
        arr = array("f", [0.0]) * n
        err = []
        if progress is not None:
            def _cb(done, total, _user):
                if err:
                    return
                try:
                    progress(done, total)
                except BaseException as e:  # ctypes は例外を握りつぶすので保存して後で投げ直す
                    err.append(e)
            cb = _PROGRESS_FN(_cb)  # 呼び出し中は参照を保持する
        else:
            cb = ctypes.cast(None, _PROGRESS_FN)
        rc = _lib.mmd_bake(h, _addr(arr), n, cb, None)
        if err:
            raise err[0]
        if rc == MMD_ERR:
            raise NativeError(_last_error())
        warn = _last_error() if rc == MMD_WARN_NAN else None
        self.last_nan_bone = nan_bone_from_message(warn) if warn else None
        return arr, warn

    def live_reset(self, world_in):
        _check_len(world_in, self._bones * 7, "world_in")
        self.live_warning = None
        self.last_nan_bone = None
        if _lib.mmd_live_reset(self._handle(), _addr(world_in)) == MMD_ERR:
            raise NativeError(_last_error())

    def live_step(self, world_in, dt):
        """1 ステップ進める。戻り値 array('f') [bone][7]。

        発散 (MMD_WARN_NAN) のときは例外にせず、self.live_warning に警告文
        ("... first at bone: <名前>; ...")、self.last_nan_bone にボーン名を入れる (それ以外は None)。"""
        _check_len(world_in, self._bones * 7, "world_in")
        out = array("f", [0.0]) * len(world_in)
        rc = _lib.mmd_live_step(self._handle(), _addr(world_in), float(dt), _addr(out))
        if rc == MMD_ERR:
            raise NativeError(_last_error())
        self.live_warning = _last_error() if rc == MMD_WARN_NAN else None
        self.last_nan_bone = nan_bone_from_message(self.live_warning) if self.live_warning else None
        return out

    def close(self):
        h, self._h = self._h, None
        if h and _lib is not None:
            _lib.mmd_destroy(h)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
