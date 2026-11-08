# ===========================================================================
# GLB (glTF binary) の extras.mmd を読み取る (Blender 版)。
#
# Unity 版 GlbPhysicsReader.cs と同じ方針:
#   - 剛体 / Joint は extras.mmd.rigidBodies / joints (PMX raw) をそのまま使う
#   - ボーンは glTF nodes + skins[0].joints から 名前 / 親 / raw PMX world 位置を復元
#   - unitScale は extras.mmd.unitScale から読む
#   - physicsGltf (変換済みビュー) は使わない。Blender の glTF インポーターが作る
#     ボーンのローカル軸は glTF ノードの軸と一致しないため、ボーンローカル値は
#     そのまま使えない。raw をワールドで組んでから Blender 座標へ写す。
#
# 例外は極力投げず、warnings に積んで読み進める。bpy には依存しない (単体テスト可)。
# ===========================================================================
import json
import os
import struct

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


class RigidBody:
    __slots__ = ("index", "name", "name_en", "bone", "group", "mask", "shape", "size",
                 "pos", "rot", "mass", "linear_damping", "angular_damping",
                 "restitution", "friction", "mode")

    def __init__(self, index, d):
        self.index = index
        self.name = d.get("name") or f"rigid_{index}"
        self.name_en = d.get("name_en") or ""
        self.bone = int(d.get("bone", -1))
        self.group = int(d.get("group", 0)) & 0xF
        # PMX の 16bit フィールド。立っているビット = 「そのグループと衝突する」。
        self.mask = int(d.get("no_collision_mask", 0xFFFF)) & 0xFFFF
        self.shape = int(d.get("shape", 0))          # 0:球 1:箱 2:カプセル
        self.size = _vec3(d.get("size"))
        self.pos = _vec3(d.get("pos"))               # raw PMX world
        self.rot = _vec3(d.get("rot"))               # ラジアン (YXZ 順)
        self.mass = float(d.get("mass", 0.0))
        self.linear_damping = float(d.get("linear_damping", 0.0))
        self.angular_damping = float(d.get("angular_damping", 0.0))
        self.restitution = float(d.get("restitution", 0.0))
        self.friction = float(d.get("friction", 0.5))
        self.mode = int(d.get("mode", 0))            # 0:追従 1:物理 2:物理+Bone合わせ

    def collides_with(self, other):
        """PMX の衝突判定 (双方向)。"""
        return bool((1 << other.group) & self.mask) and bool((1 << self.group) & other.mask)

    def bounding_radius(self):
        sx, sy, sz = self.size
        if self.shape == 0:
            return sx
        if self.shape == 1:
            return (sx * sx + sy * sy + sz * sz) ** 0.5
        return sx + sy * 0.5


class Joint:
    __slots__ = ("index", "name", "name_en", "type", "rigid_a", "rigid_b", "pos", "rot",
                 "pos_min", "pos_max", "rot_min", "rot_max", "spring_pos", "spring_rot")

    def __init__(self, index, d):
        self.index = index
        self.name = d.get("name") or f"joint_{index}"
        self.name_en = d.get("name_en") or ""
        self.type = int(d.get("type", 0))
        self.rigid_a = int(d.get("rigid_a", -1))
        self.rigid_b = int(d.get("rigid_b", -1))
        self.pos = _vec3(d.get("pos"))
        self.rot = _vec3(d.get("rot"))
        self.pos_min = _vec3(d.get("pos_min"))
        self.pos_max = _vec3(d.get("pos_max"))
        self.rot_min = _vec3(d.get("rot_min"))
        self.rot_max = _vec3(d.get("rot_max"))
        self.spring_pos = _vec3(d.get("spring_pos"))
        self.spring_rot = _vec3(d.get("spring_rot"))


class Bone:
    __slots__ = ("index", "node", "name", "name_en", "parent", "local_raw", "world_raw",
                 "flags", "layer", "ik", "extras")

    def __init__(self, index, node_index, name):
        self.index = index
        self.node = node_index
        self.name = name
        self.name_en = ""
        self.parent = -1
        self.local_raw = (0.0, 0.0, 0.0)
        self.world_raw = (0.0, 0.0, 0.0)
        self.flags = 0
        self.layer = 0
        self.ik = None
        self.extras = {}


class MaterialInfo:
    __slots__ = ("index", "name", "alpha_mode", "alpha_cutoff", "double_sided",
                 "base_texture", "extras")

    def __init__(self, index, m):
        self.index = index
        self.name = m.get("name") or f"material_{index}"
        self.alpha_mode = m.get("alphaMode", "OPAQUE")
        self.alpha_cutoff = float(m.get("alphaCutoff", 0.5))
        self.double_sided = bool(m.get("doubleSided", False))
        pbr = m.get("pbrMetallicRoughness") or {}
        bct = pbr.get("baseColorTexture")
        self.base_texture = int(bct["index"]) if bct and "index" in bct else -1
        self.extras = (m.get("extras") or {}).get("mmd") or {}

    # ---- extras.mmd の読み出しヘルパ (無ければ既定値) ----
    def g(self, key, default=None):
        return self.extras.get(key, default)

    @property
    def sphere_mode(self):
        return int(self.g("sphereMode", 0))

    @property
    def sphere_texture(self):
        return int(self.g("sphereTexture", -1))

    @property
    def toon_texture(self):
        return int(self.g("toonTexture", -1))

    @property
    def toon_shared(self):
        return int(self.g("toonShared", -1))

    @property
    def flags(self):
        return int(self.g("flags", 0))

    @property
    def has_edge(self):
        return bool(self.flags & 0x10)

    @property
    def is_double_sided(self):
        return bool(self.flags & 0x01) or self.double_sided

    @property
    def edge_color(self):
        c = self.g("edgeColor") or [0.0, 0.0, 0.0, 1.0]
        return tuple(float(x) for x in (list(c) + [1.0, 1.0, 1.0, 1.0])[:4])

    @property
    def edge_size(self):
        return float(self.g("edgeSize", 1.0))

    @property
    def alpha_class(self):
        v = self.g("alphaClass")
        if v not in ("opaque", "mask", "blend"):
            # 未知の分類は mask 相当 (仕様書の推奨)
            return "mask" if v else ("blend" if self.alpha_mode == "BLEND" else
                                     "mask" if self.alpha_mode == "MASK" else "opaque")
        return v

    @property
    def orig_texture(self):
        return int(self.g("origTexture", -1))


class MmdGlb:
    """GLB 1 ファイル分の読み取り結果。"""

    def __init__(self):
        self.path = ""
        self.json = {}
        self.bin = b""
        self.unit_scale = 1.0
        self.model_name = ""
        self.model_name_en = ""
        self.comment = ""
        self.has_mmd = False
        self.bones = []          # List[Bone]  (PMX ボーン順 = skins[0].joints 順)
        self.rigid_bodies = []   # List[RigidBody]
        self.joints = []         # List[Joint]
        self.materials = []      # List[MaterialInfo]
        self.morphs = []         # raw dicts
        self.warnings = []

    # ---- 画像 ----
    def texture_image_index(self, tex_index):
        texs = self.json.get("textures") or []
        if tex_index is None or tex_index < 0 or tex_index >= len(texs):
            return -1
        return int(texs[tex_index].get("source", -1))

    def image_info(self, image_index):
        imgs = self.json.get("images") or []
        if image_index < 0 or image_index >= len(imgs):
            return None
        return imgs[image_index]

    def image_bytes(self, image_index):
        """images[i] の生バイト列 (bufferView 同梱のみ対応)。(bytes, mime, name) か None。"""
        im = self.image_info(image_index)
        if im is None:
            return None
        name = im.get("name") or f"image_{image_index}"
        mime = im.get("mimeType") or "image/png"
        if "bufferView" in im:
            bv = (self.json.get("bufferViews") or [])[int(im["bufferView"])]
            off = int(bv.get("byteOffset", 0))
            ln = int(bv["byteLength"])
            return self.bin[off:off + ln], mime, name
        uri = im.get("uri")
        if uri:
            if uri.startswith("data:"):
                import base64
                return base64.b64decode(uri.split(",", 1)[1]), mime, name
            p = os.path.join(os.path.dirname(self.path), uri)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read(), mime, name
        return None

    def bone_by_name(self, name):
        for b in self.bones:
            if b.name == name:
                return b
        return None


# ---------------------------------------------------------------------------
def parse_glb_bytes(data):
    """GLB を (json dict, bin bytes) に分解。JSON テキスト (.gltf) も受ける。"""
    if len(data) >= 12 and struct.unpack_from("<I", data, 0)[0] == GLB_MAGIC:
        off = 12
        js = None
        bin_ = b""
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, off)
            cdata = off + 8
            if ctype == CHUNK_JSON:
                js = json.loads(data[cdata:cdata + clen].decode("utf-8"))
            elif ctype == CHUNK_BIN:
                bin_ = bytes(data[cdata:cdata + clen])
            off = cdata + clen
            if clen & 3:
                off += 4 - (clen & 3)
        if js is None:
            raise ValueError("GLB に JSON チャンクが無い")
        return js, bin_
    text = data.decode("utf-8-sig")
    js = json.loads(text)
    return js, b""


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    js, bin_ = parse_glb_bytes(data)
    model = build_model(js, bin_)
    model.path = path
    # 外部 .bin (glTF JSON) は最初の buffer だけ読む
    if not bin_:
        bufs = js.get("buffers") or []
        if bufs and bufs[0].get("uri") and not bufs[0]["uri"].startswith("data:"):
            p = os.path.join(os.path.dirname(path), bufs[0]["uri"])
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    model.bin = f.read()
    return model


def build_model(js, bin_):
    m = MmdGlb()
    m.json = js
    m.bin = bin_
    mm = ((js.get("extras") or {}).get("mmd")) or {}
    m.has_mmd = bool(mm)
    if not mm:
        m.warnings.append("extras.mmd が無い (mmd2gltf の出力ではない)。ボーンのみ読み進める。")
    scale = float(mm.get("unitScale", 0) or 0)
    if scale <= 0:
        if mm:
            m.warnings.append(f"unitScale が不正 ({scale})。1.0 として続行。")
        scale = 1.0
    m.unit_scale = scale
    m.model_name = mm.get("name") or ""
    m.model_name_en = mm.get("nameEn") or ""
    m.comment = mm.get("comment") or ""
    m.morphs = list(mm.get("morphs") or [])

    # --- ボーン (nodes + skins[0].joints) ---
    nodes = js.get("nodes") or []
    skins = js.get("skins") or []
    joint_nodes = list(skins[0].get("joints") or []) if skins else []
    if not joint_nodes:
        m.warnings.append("skins/joints が無いためボーン 0 件で続行。")
    node_to_bone = {}
    for i, ni in enumerate(joint_nodes):
        node = nodes[ni] if 0 <= ni < len(nodes) else None
        name = (node.get("name") if node else None) or f"bone_{i}"
        b = Bone(i, ni, name)
        if node is not None:
            lt = _vec3(node.get("translation"))
            b.local_raw = (lt[0] / scale, lt[1] / scale, -lt[2] / scale)  # glTF -> raw PMX
            ex = ((node.get("extras") or {}).get("mmd")) or {}
            b.extras = ex
            b.name_en = ex.get("nameEn") or ""
            b.flags = int(ex.get("flags", 0))
            b.layer = int(ex.get("layer", 0))
            b.ik = ex.get("ik")
        m.bones.append(b)
        node_to_bone[ni] = i
    # 親: children 走査
    for ni, node in enumerate(nodes):
        for c in node.get("children") or []:
            if c in node_to_bone:
                m.bones[node_to_bone[c]].parent = node_to_bone.get(ni, -1)
    # world = ローカル並進の階層合成 (バインド回転恒等)
    cache = {}

    def world(i, depth=0):
        if i in cache:
            return cache[i]
        b = m.bones[i]
        p = b.parent
        if p < 0 or depth > 1024:
            w = b.local_raw
        else:
            pw = world(p, depth + 1)
            w = (pw[0] + b.local_raw[0], pw[1] + b.local_raw[1], pw[2] + b.local_raw[2])
        cache[i] = w
        return w

    for i in range(len(m.bones)):
        m.bones[i].world_raw = world(i)

    # --- 剛体 / Joint (raw) ---
    for i, d in enumerate(mm.get("rigidBodies") or []):
        m.rigid_bodies.append(RigidBody(i, d))
    for i, d in enumerate(mm.get("joints") or []):
        m.joints.append(Joint(i, d))
    nb = len(m.bones)
    nr = len(m.rigid_bodies)
    for rb in m.rigid_bodies:
        if rb.bone >= nb:
            m.warnings.append(f"剛体 '{rb.name}' の関連ボーン index {rb.bone} が範囲外 (ボーン数 {nb})。ワールド固定扱い。")
            rb.bone = -1
    for j in m.joints:
        if not (0 <= j.rigid_a < nr and 0 <= j.rigid_b < nr):
            m.warnings.append(f"Joint '{j.name}' が存在しない剛体を参照 (A={j.rigid_a} B={j.rigid_b}, 剛体数 {nr})。スキップ。")

    # --- マテリアル ---
    for i, mat in enumerate(js.get("materials") or []):
        m.materials.append(MaterialInfo(i, mat))
    return m


def _vec3(v):
    if not v or len(v) < 3:
        return (0.0, 0.0, 0.0)
    return (float(v[0]), float(v[1]), float(v[2]))
