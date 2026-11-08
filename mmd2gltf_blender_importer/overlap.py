# ===========================================================================
# 静止姿勢で重なっている剛体ペアの検出 (球 / 箱 / カプセル)。
#
# MMD のモデルは、髪の根元やスカートの上段が体のコライダーの内側に入った状態で
# 静止姿勢が作られている (Unity 版 UnityMapping.md「揺れ物の根元は体に埋まっているのが前提」)。
# Blender の Bullet はこれを押し出すので、再生と同時にスカートが外へ弾かれる。
# 起動時に重なっている組は衝突を無効化する (Unity 版は離れた時点で復帰させるが、
# Blender の拘束はシミュレーション中に切り替えられないので恒久的に無効化する)。
#
# 形状は Blender ローカル: 球 r / 箱 半寸法 (hx, hy, hz) / カプセル 半径 r・長軸 Z・円筒高 h。
# 判定は近似 (線分↔箱はサンプル点)。shrink で寸法を縮めて、かすっている程度の接触は無視する。
# ===========================================================================
from mathutils import Vector, Matrix


class Shape:
    __slots__ = ("kind", "matrix", "inv", "r", "h", "half")

    def __init__(self, kind, matrix, r=0.0, h=0.0, half=(0.0, 0.0, 0.0)):
        self.kind = kind            # "sphere" | "box" | "capsule"
        self.matrix = matrix
        self.inv = matrix.inverted()
        self.r = r
        self.h = h
        self.half = Vector(half)

    def segment(self):
        c = self.matrix.translation
        if self.kind == "capsule":
            z = (self.matrix.to_3x3() @ Vector((0, 0, 1))).normalized() * (self.h * 0.5)
            return c - z, c + z
        return c, c

    def bounding_radius(self):
        if self.kind == "sphere":
            return self.r
        if self.kind == "capsule":
            return self.r + self.h * 0.5
        return self.half.length


def shape_from_rigid(rb, matrix, scale, shrink=1.0):
    sx, sy, sz = rb.size
    if rb.shape == 0:
        return Shape("sphere", matrix, r=sx * scale * shrink)
    if rb.shape == 1:
        return Shape("box", matrix, half=(sx * scale * shrink, sz * scale * shrink, sy * scale * shrink))
    return Shape("capsule", matrix, r=sx * scale * shrink, h=max(0.0, sy * scale * shrink))


def _seg_seg_dist(p0, p1, q0, q1):
    u = p1 - p0
    v = q1 - q0
    w = p0 - q0
    a, b, c, d, e = u.dot(u), u.dot(v), v.dot(v), u.dot(w), v.dot(w)
    den = a * c - b * b
    s = t = 0.0
    if den > 1e-12:
        s = max(0.0, min(1.0, (b * e - c * d) / den))
    t_num = b * s + e
    if c > 1e-12:
        t = max(0.0, min(1.0, t_num / c))
    else:
        t = 0.0
    if c > 1e-12:
        s2 = (b * t - d) / a if a > 1e-12 else 0.0
        s = max(0.0, min(1.0, s2))
    return ((p0 + u * s) - (q0 + v * t)).length


def _point_box_dist(p_local, half):
    dx = max(abs(p_local.x) - half.x, 0.0)
    dy = max(abs(p_local.y) - half.y, 0.0)
    dz = max(abs(p_local.z) - half.z, 0.0)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _seg_box_dist(p0, p1, box, samples=24):
    inv = box.inv
    a = inv @ p0
    b = inv @ p1
    best = 1e9
    for i in range(samples + 1):
        t = i / samples
        best = min(best, _point_box_dist(a.lerp(b, t), box.half))
        if best <= 0.0:
            return 0.0
    return best


def _box_box_overlap(A, B):
    """分離軸判定 (15 軸)。重なっていれば True。"""
    ra = A.matrix.to_3x3()
    rb = B.matrix.to_3x3()
    axes_a = [ra.col[i].normalized() for i in range(3)]
    axes_b = [rb.col[i].normalized() for i in range(3)]
    d = B.matrix.translation - A.matrix.translation
    tests = list(axes_a) + list(axes_b)
    for i in range(3):
        for j in range(3):
            c = axes_a[i].cross(axes_b[j])
            if c.length > 1e-6:
                tests.append(c.normalized())
    for ax in tests:
        pa = sum(abs(ax.dot(axes_a[i])) * A.half[i] for i in range(3))
        pb = sum(abs(ax.dot(axes_b[i])) * B.half[i] for i in range(3))
        if abs(d.dot(ax)) > pa + pb:
            return False
    return True


def overlaps(A, B):
    # 早期棄却
    if (A.matrix.translation - B.matrix.translation).length > A.bounding_radius() + B.bounding_radius():
        return False
    ka, kb = A.kind, B.kind
    if ka == "box" and kb == "box":
        return _box_box_overlap(A, B)
    if ka == "box" or kb == "box":
        box, other = (A, B) if ka == "box" else (B, A)
        p0, p1 = other.segment()
        return _seg_box_dist(p0, p1, box) < other.r
    p0, p1 = A.segment()
    q0, q1 = B.segment()
    return _seg_seg_dist(p0, p1, q0, q1) < A.r + B.r
