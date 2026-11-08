# ===========================================================================
# materials[i].extras.mmd → Blender ノードマテリアル (トゥーン / スフィア / 輪郭線 / α復元)。
#
# Unity 版 (lilToon 変換) に相当する部分。Blender では EEVEE 向けに
#   色 = Base × Toon(N·L) (× or +) Sphere(カメラ空間法線)
# を Emission に流し、α で Transparent と混ぜる。
#   - トゥーン: Diffuse BSDF → Shader to RGB (EEVEE 専用) の明るさを V 座標にして
#     トゥーン画像を引く。Cycles では Shader to RGB が使えないため陰影は出ない。
#   - スフィア: カメラ空間法線 (Vector Transform) の xy を UV に写す (MatCap)。
#     sphereMode 1=乗算 2=加算 3=サブテクスチャ(未対応・無視)。
#   - 輪郭線: Solidify (法線反転・裏面カリング) + 材質ごとの edge 材質。
#     edge フラグ (flags bit4) の無い材質は透明な edge 材質を当てる。
#   - α: alphaClass "blend" / "mask" とも origTexture (無加工) の実 α を DITHERED (ハッシュ) で描く。
#        BLENDED は深度を書かず、輪郭線シェルが透けて色が濁るため使わない。
#   - ambient / specular / specularPower は未対応 (Unity 版と同じ)。
# ===========================================================================
import os
import re
import tempfile

import bpy

GROUP_COORD = "MMD2GLTF Coord"
GROUP_SHADER = "MMD2GLTF Shader"
EDGE_MOD_NAME = "mmd2gltf_edge"
EDGE_SUFFIX = "_mmd_edge"
PROP_MAT_INDEX = "mmd2gltf_material"


class MaterialOptions:
    def __init__(self):
        self.use_orig_texture = True     # origTexture (無加工) を優先
        self.build_toon = True
        self.build_sphere = True
        self.build_edge = True
        self.edge_scale = 0.03           # edgeSize 1.0 あたりの太さ (PMX 単位)。× unitScale
        self.edge_skip_ratio = 0.35      # 部品の最小寸法 × この比率より厚い輪郭線は付けない (小物が黒く潰れる対策)
        self.shared_toon_dir = ""        # toon01.bmp..toon10.bmp の置き場 (空なら生成)


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------
def _safe_name(s):
    return re.sub(r"[^\w.\-]+", "_", s)


def _cache_dir(model):
    base = os.path.splitext(os.path.basename(model.path or "glb"))[0]
    d = os.path.join(tempfile.gettempdir(), "mmd2gltf_textures", _safe_name(base))
    os.makedirs(d, exist_ok=True)
    return d


def get_image_for_texture(model, tex_index, report=None):
    """glTF textures[tex_index] → bpy Image。無ければ GLB から取り出して読み込む。"""
    img_index = model.texture_image_index(tex_index)
    if img_index < 0:
        return None
    info = model.image_info(img_index)
    name = (info or {}).get("name") or f"image_{img_index}"
    # インポーターが読み込み済みならそれを使う (名前で照合)
    img = bpy.data.images.get(name)
    if img is not None and img.size[0] > 0:
        return img
    data = model.image_bytes(img_index)
    if data is None:
        if report:
            report("WARNING", f"画像 {name} (index {img_index}) を GLB から取り出せませんでした。")
        return None
    raw, mime, _ = data
    ext = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
    path = os.path.join(_cache_dir(model), f"{img_index:03d}_{_safe_name(name)}{ext}")
    with open(path, "wb") as f:
        f.write(raw)
    img = bpy.data.images.load(path, check_existing=True)
    img.name = name
    try:
        img.pack()
    except RuntimeError:
        pass
    return img


def _generated_toon(n):
    """共有トゥーンが無いときの代替 (32x32 の上=明・下=暗の階調)。"""
    name = f"toon{n + 1:02d}_generated"
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    img = bpy.data.images.new(name, 32, 32, alpha=False)
    shade = (0.80, 0.80, 0.88) if n in (0, 1, 2, 3) else (0.70, 0.72, 0.85)
    px = []
    for y in range(32):            # y=0 が下 (影側)
        t = min(1.0, max(0.0, (y - 12) / 6.0))
        r = shade[0] + (1 - shade[0]) * t
        g = shade[1] + (1 - shade[1]) * t
        b = shade[2] + (1 - shade[2]) * t
        for _ in range(32):
            px.extend((r, g, b, 1.0))
    img.pixels = px
    img.pack()
    return img


def get_shared_toon(n, shared_dir):
    """toonShared n (0 始まり) → Image。ディレクトリに toon0N.bmp があれば読む。"""
    if n < 0:
        return None
    fname = f"toon{n + 1:02d}.bmp"
    if shared_dir:
        p = os.path.join(bpy.path.abspath(shared_dir), fname)
        if os.path.isfile(p):
            img = bpy.data.images.load(p, check_existing=True)
            return img
    return _generated_toon(n)


# ---------------------------------------------------------------------------
# ノードグループ
# ---------------------------------------------------------------------------
def _new_socket(tree, in_out, name, stype, default=None):
    s = tree.interface.new_socket(name=name, in_out=in_out, socket_type=stype)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    return s


def ensure_coord_group():
    g = bpy.data.node_groups.get(GROUP_COORD)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(GROUP_COORD, "ShaderNodeTree")
    _new_socket(g, "OUTPUT", "Toon UV", "NodeSocketVector")
    _new_socket(g, "OUTPUT", "Sphere UV", "NodeSocketVector")
    n = g.nodes
    out = n.new("NodeGroupOutput")
    out.location = (600, 0)
    # --- toon: 拡散照明の明るさ → V ---
    diff = n.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (1, 1, 1, 1)
    diff.inputs["Roughness"].default_value = 0.0
    diff.location = (-400, 200)
    s2rgb = n.new("ShaderNodeShaderToRGB")
    s2rgb.location = (-200, 200)
    bw = n.new("ShaderNodeRGBToBW")
    bw.location = (0, 200)
    clamp = n.new("ShaderNodeClamp")
    clamp.location = (150, 200)
    comb_t = n.new("ShaderNodeCombineXYZ")
    comb_t.inputs["X"].default_value = 0.5
    comb_t.inputs["Z"].default_value = 0.0
    comb_t.location = (350, 200)
    g.links.new(diff.outputs[0], s2rgb.inputs[0])
    g.links.new(s2rgb.outputs["Color"], bw.inputs[0])
    g.links.new(bw.outputs[0], clamp.inputs["Value"])
    g.links.new(clamp.outputs[0], comb_t.inputs["Y"])
    g.links.new(comb_t.outputs[0], out.inputs["Toon UV"])
    # --- sphere: カメラ空間法線 → UV ---
    geo = n.new("ShaderNodeNewGeometry")
    geo.location = (-400, -200)
    vt = n.new("ShaderNodeVectorTransform")
    vt.vector_type = "NORMAL"
    vt.convert_from = "WORLD"
    vt.convert_to = "CAMERA"
    vt.location = (-200, -200)
    sep = n.new("ShaderNodeSeparateXYZ")
    sep.location = (0, -200)
    mx = n.new("ShaderNodeMath")
    mx.operation = "MULTIPLY_ADD"
    mx.inputs[1].default_value = 0.5
    mx.inputs[2].default_value = 0.5
    mx.location = (150, -150)
    my = n.new("ShaderNodeMath")
    my.operation = "MULTIPLY_ADD"
    my.inputs[1].default_value = 0.5
    my.inputs[2].default_value = 0.5
    my.location = (150, -300)
    comb_s = n.new("ShaderNodeCombineXYZ")
    comb_s.location = (350, -200)
    g.links.new(geo.outputs["Normal"], vt.inputs[0])
    g.links.new(vt.outputs[0], sep.inputs[0])
    g.links.new(sep.outputs["X"], mx.inputs[0])
    g.links.new(sep.outputs["Y"], my.inputs[0])
    g.links.new(mx.outputs[0], comb_s.inputs["X"])
    g.links.new(my.outputs[0], comb_s.inputs["Y"])
    g.links.new(comb_s.outputs[0], out.inputs["Sphere UV"])
    return g


def ensure_shader_group():
    g = bpy.data.node_groups.get(GROUP_SHADER)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(GROUP_SHADER, "ShaderNodeTree")
    _new_socket(g, "INPUT", "Base Color", "NodeSocketColor", (1, 1, 1, 1))
    _new_socket(g, "INPUT", "Alpha", "NodeSocketFloat", 1.0)
    _new_socket(g, "INPUT", "Toon Color", "NodeSocketColor", (1, 1, 1, 1))
    _new_socket(g, "INPUT", "Use Toon", "NodeSocketFloat", 0.0)
    _new_socket(g, "INPUT", "Sphere Color", "NodeSocketColor", (1, 1, 1, 1))
    _new_socket(g, "INPUT", "Sphere Mode", "NodeSocketFloat", 0.0)
    _new_socket(g, "OUTPUT", "Shader", "NodeSocketShader")
    n = g.nodes
    inp = n.new("NodeGroupInput")
    inp.location = (-800, 0)
    out = n.new("NodeGroupOutput")
    out.location = (700, 0)

    def mix(blend, loc):
        m = n.new("ShaderNodeMix")
        m.data_type = "RGBA"
        m.blend_type = blend
        m.clamp_factor = True
        m.location = loc
        return m

    toon = mix("MULTIPLY", (-500, 100))
    g.links.new(inp.outputs["Use Toon"], toon.inputs["Factor"])
    g.links.new(inp.outputs["Base Color"], toon.inputs["A"])
    g.links.new(inp.outputs["Toon Color"], toon.inputs["B"])

    sph_mul = mix("MULTIPLY", (-250, 200))
    sph_mul.inputs["Factor"].default_value = 1.0
    g.links.new(toon.outputs["Result"], sph_mul.inputs["A"])
    g.links.new(inp.outputs["Sphere Color"], sph_mul.inputs["B"])
    sph_add = mix("ADD", (-250, -50))
    sph_add.inputs["Factor"].default_value = 1.0
    g.links.new(toon.outputs["Result"], sph_add.inputs["A"])
    g.links.new(inp.outputs["Sphere Color"], sph_add.inputs["B"])

    is1 = n.new("ShaderNodeMath")
    is1.operation = "COMPARE"
    is1.inputs[1].default_value = 1.0
    is1.inputs[2].default_value = 0.1
    is1.location = (-250, -300)
    is2 = n.new("ShaderNodeMath")
    is2.operation = "COMPARE"
    is2.inputs[1].default_value = 2.0
    is2.inputs[2].default_value = 0.1
    is2.location = (-250, -450)
    g.links.new(inp.outputs["Sphere Mode"], is1.inputs[0])
    g.links.new(inp.outputs["Sphere Mode"], is2.inputs[0])

    sel1 = mix("MIX", (0, 100))
    g.links.new(is1.outputs[0], sel1.inputs["Factor"])
    g.links.new(toon.outputs["Result"], sel1.inputs["A"])
    g.links.new(sph_mul.outputs["Result"], sel1.inputs["B"])
    sel2 = mix("MIX", (200, 100))
    g.links.new(is2.outputs[0], sel2.inputs["Factor"])
    g.links.new(sel1.outputs["Result"], sel2.inputs["A"])
    g.links.new(sph_add.outputs["Result"], sel2.inputs["B"])

    em = n.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    em.location = (400, 100)
    g.links.new(sel2.outputs["Result"], em.inputs["Color"])
    tr = n.new("ShaderNodeBsdfTransparent")
    tr.location = (400, -100)
    mixs = n.new("ShaderNodeMixShader")
    mixs.location = (550, 0)
    g.links.new(inp.outputs["Alpha"], mixs.inputs["Fac"])
    g.links.new(tr.outputs[0], mixs.inputs[1])
    g.links.new(em.outputs[0], mixs.inputs[2])
    g.links.new(mixs.outputs[0], out.inputs["Shader"])
    return g


# ---------------------------------------------------------------------------
# マテリアル割り当ての解決
# ---------------------------------------------------------------------------
def collect_mesh_objects(arm):
    """アーマチュアに紐づくメッシュ (Armature モディファイア or 親)。"""
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if o.parent == arm:
            out.append(o)
            continue
        for m in o.modifiers:
            if m.type == "ARMATURE" and m.object == arm:
                out.append(o)
                break
    return out


def resolve_materials(model, mesh_objects):
    """glTF material index → bpy.types.Material。名前 (+重複サフィックス) で照合。"""
    used = {}
    for o in mesh_objects:
        for slot in o.material_slots:
            if slot.material is not None:
                used[slot.material.name] = slot.material
    result = {}
    taken = set()
    for mi in model.materials:
        cand = None
        if mi.name in used and mi.name not in taken:
            cand = used[mi.name]
        else:
            for k in range(1, 100):
                nm = f"{mi.name}.{k:03d}"
                if nm in used and nm not in taken:
                    cand = used[nm]
                    break
        if cand is None:
            # 既にこのアドオンが index を書いている材質を探す
            for mat in used.values():
                if mat.get(PROP_MAT_INDEX) == mi.index:
                    cand = mat
                    break
        if cand is not None:
            result[mi.index] = cand
            taken.add(cand.name)
            cand[PROP_MAT_INDEX] = mi.index
    return result


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def _base_color_factor(model, index):
    mats = model.json.get("materials") or []
    if 0 <= index < len(mats):
        f = (mats[index].get("pbrMetallicRoughness") or {}).get("baseColorFactor")
        if f and len(f) >= 4:
            return tuple(float(x) for x in f[:4])
    return (1.0, 1.0, 1.0, 1.0)


def build_material(mat, mi, model, opts: MaterialOptions, report=None):
    coord_g = ensure_coord_group()
    shader_g = ensure_shader_group()
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    n = nt.nodes
    out = n.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    shader = n.new("ShaderNodeGroup")
    shader.node_tree = shader_g
    shader.location = (350, 0)
    nt.links.new(shader.outputs["Shader"], out.inputs["Surface"])
    coord = n.new("ShaderNodeGroup")
    coord.node_tree = coord_g
    coord.location = (-600, -250)
    uv = n.new("ShaderNodeUVMap")
    uv.location = (-600, 200)

    # --- ベースカラー ---
    tex_index = mi.base_texture
    if opts.use_orig_texture and mi.orig_texture >= 0 and mi.alpha_class in ("mask", "blend"):
        tex_index = mi.orig_texture
    factor = _base_color_factor(model, mi.index)
    base_img = get_image_for_texture(model, tex_index, report) if tex_index >= 0 else None
    color_src = None
    alpha_src = None
    if base_img is not None:
        bt = n.new("ShaderNodeTexImage")
        bt.image = base_img
        bt.interpolation = "Linear"
        bt.location = (-350, 200)
        bt.label = "Base"
        nt.links.new(uv.outputs[0], bt.inputs["Vector"])
        color_src = bt.outputs["Color"]
        alpha_src = bt.outputs["Alpha"]
    mulc = n.new("ShaderNodeMix")
    mulc.data_type = "RGBA"
    mulc.blend_type = "MULTIPLY"
    mulc.inputs["Factor"].default_value = 1.0
    mulc.inputs["B"].default_value = factor
    mulc.location = (-50, 200)
    mulc.label = "Base Color Factor"
    if color_src is not None:
        nt.links.new(color_src, mulc.inputs["A"])
    else:
        mulc.inputs["A"].default_value = (1, 1, 1, 1)
    nt.links.new(mulc.outputs["Result"], shader.inputs["Base Color"])
    mula = n.new("ShaderNodeMath")
    mula.operation = "MULTIPLY"
    mula.inputs[1].default_value = factor[3]
    mula.location = (-50, 0)
    mula.label = "Alpha"
    if alpha_src is not None:
        nt.links.new(alpha_src, mula.inputs[0])
    else:
        mula.inputs[0].default_value = 1.0
    nt.links.new(mula.outputs[0], shader.inputs["Alpha"])

    # --- トゥーン ---
    toon_img = None
    if opts.build_toon:
        if mi.toon_texture >= 0:
            toon_img = get_image_for_texture(model, mi.toon_texture, report)
        elif mi.toon_shared >= 0:
            toon_img = get_shared_toon(mi.toon_shared, opts.shared_toon_dir)
    if toon_img is not None:
        tt = n.new("ShaderNodeTexImage")
        tt.image = toon_img
        tt.interpolation = "Linear"
        tt.extension = "EXTEND"
        tt.location = (-350, -150)
        tt.label = "Toon"
        nt.links.new(coord.outputs["Toon UV"], tt.inputs["Vector"])
        nt.links.new(tt.outputs["Color"], shader.inputs["Toon Color"])
        shader.inputs["Use Toon"].default_value = 1.0
    else:
        shader.inputs["Use Toon"].default_value = 0.0

    # --- スフィア ---
    sphere_img = None
    if opts.build_sphere and mi.sphere_mode in (1, 2) and mi.sphere_texture >= 0:
        sphere_img = get_image_for_texture(model, mi.sphere_texture, report)
    if mi.sphere_mode == 3 and report:
        report("WARNING", f"材質 '{mi.name}': sphereMode=3 (サブテクスチャ) は未対応のためスキップ。")
    if sphere_img is not None:
        st = n.new("ShaderNodeTexImage")
        st.image = sphere_img
        st.interpolation = "Linear"
        st.extension = "EXTEND"
        st.location = (-350, -450)
        st.label = "Sphere"
        nt.links.new(coord.outputs["Sphere UV"], st.inputs["Vector"])
        nt.links.new(st.outputs["Color"], shader.inputs["Sphere Color"])
        shader.inputs["Sphere Mode"].default_value = float(mi.sphere_mode)
    else:
        shader.inputs["Sphere Mode"].default_value = 0.0

    # --- 描画設定 ---
    # "blend" も DITHERED (ハッシュ透過) にする。BLENDED は深度を書かないため、半透明の髪の
    # 下にある輪郭線シェルや内側の面が透けて色が濁る (実測: IA の淡い髪が濃いマゼンタに見えた)。
    # MMD は髪も深度を書くので、深度を書くハッシュ透過のほうが見た目が近い。
    # 実アルファのブレンドが必要なら手動で Blended に切り替えられる。
    mat.surface_render_method = "DITHERED"
    mat.use_backface_culling = not mi.is_double_sided
    mat.use_backface_culling_shadow = False
    mat["mmd2gltf_alpha_class"] = mi.alpha_class
    mat[PROP_MAT_INDEX] = mi.index


def _edge_material(src_mat, mi, has_edge):
    name = src_mat.name + EDGE_SUFFIX
    em = bpy.data.materials.get(name)
    if em is None:
        em = bpy.data.materials.new(name)
    em.use_nodes = True
    nt = em.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    if has_edge:
        e = nt.nodes.new("ShaderNodeEmission")
        c = mi.edge_color
        e.inputs["Color"].default_value = (c[0], c[1], c[2], 1.0)
        e.inputs["Strength"].default_value = 1.0
        if c[3] < 0.999:
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            mx = nt.nodes.new("ShaderNodeMixShader")
            mx.inputs["Fac"].default_value = c[3]
            nt.links.new(tr.outputs[0], mx.inputs[1])
            nt.links.new(e.outputs[0], mx.inputs[2])
            nt.links.new(mx.outputs[0], out.inputs["Surface"])
            em.surface_render_method = "BLENDED"
        else:
            nt.links.new(e.outputs[0], out.inputs["Surface"])
            em.surface_render_method = "DITHERED"
    else:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        nt.links.new(tr.outputs[0], out.inputs["Surface"])
        em.surface_render_method = "BLENDED"
    em.use_backface_culling = True
    em.use_backface_culling_shadow = True
    em["mmd2gltf_edge"] = True
    return em


def remove_edge(mesh_objects):
    for o in mesh_objects:
        mod = o.modifiers.get(EDGE_MOD_NAME)
        if mod is not None:
            o.modifiers.remove(mod)
        # 末尾の edge 材質スロットを外す
        while o.material_slots and o.material_slots[-1].material is not None \
                and o.material_slots[-1].material.get("mmd2gltf_edge"):
            with bpy.context.temp_override(object=o, active_object=o):
                o.active_material_index = len(o.material_slots) - 1
                bpy.ops.object.material_slot_remove()


def _material_min_extent(obj):
    """材質スロットごとの AABB 最小辺長 (ワールドスケール込み)。輪郭線の太さ判定用。"""
    me = obj.data
    n = len(obj.material_slots)
    mins = [[1e9, 1e9, 1e9] for _ in range(n)]
    maxs = [[-1e9, -1e9, -1e9] for _ in range(n)]
    verts = me.vertices
    sc = obj.matrix_world.to_scale()
    for poly in me.polygons:
        mi = poly.material_index
        if mi >= n:
            continue
        for vi in poly.vertices:
            co = verts[vi].co
            for k in range(3):
                v = co[k] * sc[k]
                if v < mins[mi][k]:
                    mins[mi][k] = v
                if v > maxs[mi][k]:
                    maxs[mi][k] = v
    out = []
    for i in range(n):
        if mins[i][0] > 1e8:
            out.append(0.0)
        else:
            out.append(min(maxs[i][k] - mins[i][k] for k in range(3)))
    return out


def build_edge(mesh_objects, mat_map, model, opts: MaterialOptions, report=None):
    scale = model.unit_scale
    log = report or (lambda *a, **k: None)
    for o in mesh_objects:
        remove_edge([o])
        n_orig = len(o.material_slots)
        if n_orig == 0:
            continue
        extents = _material_min_extent(o)
        # まず太さを決める (edge フラグのある材質の平均)
        edge_sizes = []
        infos = []
        for slot in list(o.material_slots)[:n_orig]:
            mat = slot.material
            mi = None
            if mat is not None and mat.get(PROP_MAT_INDEX) is not None:
                idx = int(mat[PROP_MAT_INDEX])
                if 0 <= idx < len(model.materials):
                    mi = model.materials[idx]
            infos.append((mat, mi))
            if mi is not None and mi.has_edge:
                edge_sizes.append(mi.edge_size)
        if not edge_sizes:
            continue
        mean_size = sum(edge_sizes) / len(edge_sizes)
        thickness = mean_size * opts.edge_scale * scale
        any_edge = False
        skipped = []
        for si, (mat, mi) in enumerate(infos):
            has_edge = bool(mi and mi.has_edge)
            # 小物対策: 部品の最小寸法に対してシェルが厚すぎる材質は輪郭線を付けない
            if has_edge and extents[si] > 0.0 and thickness > extents[si] * opts.edge_skip_ratio:
                has_edge = False
                skipped.append(mat.name if mat else str(si))
            if has_edge:
                any_edge = True
            em = _edge_material(mat, mi, has_edge) if mat is not None else None
            o.data.materials.append(em)
        if skipped:
            log("INFO", f"輪郭線を省いた材質 (部品が小さい): {', '.join(skipped)}")
        if not any_edge:
            remove_edge([o])
            continue
        mod = o.modifiers.new(EDGE_MOD_NAME, "SOLIDIFY")
        mod.thickness = -thickness
        mod.offset = 1.0
        mod.use_flip_normals = True
        mod.use_rim = False
        mod.use_even_offset = False
        mod.material_offset = n_orig
        mod.material_offset_rim = n_orig
        mod.show_in_editmode = False
        # アーマチュアの後ろに置く (変形後に押し出す)
        try:
            with bpy.context.temp_override(object=o, active_object=o):
                bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=len(o.modifiers) - 1)
        except RuntimeError:
            pass


def build(context, arm, model, opts: MaterialOptions, report=None):
    log = report or (lambda *a, **k: None)
    meshes = collect_mesh_objects(arm)
    if not meshes:
        log("WARNING", "アーマチュアに紐づくメッシュが見つかりません。")
        return {"materials": 0, "meshes": 0}
    mat_map = resolve_materials(model, meshes)
    n = 0
    for mi in model.materials:
        mat = mat_map.get(mi.index)
        if mat is None:
            log("WARNING", f"材質 '{mi.name}' (index {mi.index}) に対応する Blender マテリアルが見つかりません。")
            continue
        build_material(mat, mi, model, opts, log)
        n += 1
    if opts.build_edge:
        build_edge(meshes, mat_map, model, opts, log)
    else:
        remove_edge(meshes)
    return {"materials": n, "meshes": len(meshes)}
