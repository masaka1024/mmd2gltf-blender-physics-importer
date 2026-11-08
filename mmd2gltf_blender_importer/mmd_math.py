# ===========================================================================
# PMX raw 座標 → Blender 座標の変換。
#
# 経路: PMX (左手系 Y-up, 未スケール)
#        → glTF: 位置 (x, y, -z)·s / quat (-x, -y, z, w)   … mmd2gltf の約束
#        → Blender: glTF (x, y, z) → (x, -z, y)              … Blender glTF インポーターの Y-up→Z-up
#   合成: 位置 (x, y, z) → (x, z, y)·s   (y と z の入れ替え = 鏡映)
#   回転: R_b = P · R_p · P   (P = y/z 入替。P は自身が逆行列)
#         鏡映で共役すると「軸は P で写り、角度の符号が反転」する。
#
# PMX 剛体/Joint のオイラー角は engine (Quat.FromEulerYxz) と同じ q = qy·qx·qz、
# すなわち R = Ry·Rx·Rz (Z→X→Y の順に適用)。Blender の Euler モードでは 'ZXY'。
# ===========================================================================
from mathutils import Matrix, Vector, Euler, Quaternion

# PMX → Blender の軸入替 (3x3)。det = -1 (鏡映)。
P3 = Matrix(((1.0, 0.0, 0.0),
             (0.0, 0.0, 1.0),
             (0.0, 1.0, 0.0)))


def pmx_euler_matrix(rot):
    """PMX (rx, ry, rz) ラジアン → 3x3 回転行列 (R = Ry·Rx·Rz)。"""
    return Euler((rot[0], rot[1], rot[2]), "ZXY").to_matrix()


def pmx_euler_quat(rot):
    """engine の Quat.FromEulerYxz と同じ (x, y, z, w) を返す (検証用)。"""
    import math
    cx, sx = math.cos(rot[0] * 0.5), math.sin(rot[0] * 0.5)
    cy, sy = math.cos(rot[1] * 0.5), math.sin(rot[1] * 0.5)
    cz, sz = math.cos(rot[2] * 0.5), math.sin(rot[2] * 0.5)
    return (cy * sx * cz + sy * cx * sz,
            sy * cx * cz - cy * sx * sz,
            cy * cx * sz - sy * sx * cz,
            cy * cx * cz + sy * sx * sz)


def pmx_pos_to_blender(pos, scale):
    return Vector((pos[0] * scale, pos[2] * scale, pos[1] * scale))


def pmx_rot_to_blender(rot):
    """PMX オイラー → Blender 3x3 回転 (鏡映共役)。"""
    return P3 @ pmx_euler_matrix(rot) @ P3


def pmx_world_matrix(pos, rot, scale):
    """PMX raw の位置・回転 → Blender ワールド 4x4。"""
    m = pmx_rot_to_blender(rot).to_4x4()
    m.translation = pmx_pos_to_blender(pos, scale)
    return m


def pmx_axis_vector_to_blender(v):
    """方向ベクトル (スケール無し・符号そのまま)。"""
    return Vector((v[0], v[2], v[1]))


def blender_pos_to_pmx(v, scale):
    return (v.x / scale, v.z / scale, v.y / scale)


def swap_yz(v):
    return (v[0], v[2], v[1])


def angular_limit_to_blender(lo, hi):
    """PMX 回転制限 (lo, hi) [各軸ラジアン] → Blender 軸順 (x, y, z) の (lo, hi)。
    PMX X 回転 → Blender X (角度符号反転)、PMX Y → Blender Z、PMX Z → Blender Y。
    lo > hi は「自由」を意味する (Bullet 流)。その軸は None を返す。"""
    out = []
    for src in (0, 2, 1):            # Blender x,y,z に対応する PMX 軸
        a, b = lo[src], hi[src]
        if a > b:
            out.append(None)
        else:
            out.append((-b, -a))
    return out


def linear_limit_to_blender(lo, hi, scale):
    """PMX 並進制限 → Blender (x, y, z)。並進は符号反転なし、y/z 入替、スケール適用。"""
    out = []
    for src in (0, 2, 1):
        a, b = lo[src], hi[src]
        if a > b:
            out.append(None)
        else:
            out.append((a * scale, b * scale))
    return out
