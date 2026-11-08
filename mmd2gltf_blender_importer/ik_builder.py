# ===========================================================================
# nodes[i].extras.mmd.ik から Blender の IK 拘束を組み直す (任意機能)。
#
# PMX の IK:
#   ik ボーン (位置目標) / target = エフェクタボーン / links = エフェクタ側から根元へ並ぶ鎖
# Blender の IK:
#   鎖の末端ボーン (links[0]) に IK 拘束を置き、chain_count = len(links)、target = ik ボーン。
#   Blender インポーターはボーンの尾を子へ向けるので、links[0] の尾 = エフェクタの頭になる。
#
# 角度制限: PMX の制限はバインド時の PMX グローバル軸基準 (ボーン回転恒等)。
#   Blender のボーンローカル軸へ写すのは近似で、「PMX 軸に最も近いローカル軸」に制限を置く。
#   PMX X 回転 θ は Blender では X 回転 -θ (鏡映) なので範囲は (-max, -min)。
#   実測 (Tda 式モデルの膝): +X ローカル回転 = 屈曲 (足首が後ろ上へ)。制限 [0.5°, 180°] で
#   屈曲だけを許可できる。
#
# 膝の特異点: MMD モデルの脚はまっすぐで、制限の境界 (0°) ちょうどにある。
#   Blender のレガシー IK ソルバは伸び切った姿勢から「過伸展 (-X)」側へ曲げようとし、
#   制限に阻まれてまったく動かない (実測: 目標を 25cm 上げても膝 2cm)。
#   MMD の CCD は膝を特別扱いして回避している。ここでは制限付きリンクのポーズに小さな
#   初期屈曲 (既定 10°) を入れて、ソルバの初期勾配を許可側へ向ける (5° では前上方の目標で失敗、10° で成功)。IK ソルバは毎回ポーズ基底
#   から解き始めるので、この値が常に初期推定になる。静止時の膝位置のずれは 2mm 程度。
#   ポールターゲットも試したが、レガシー/iTaSC とも大腿が 180° 捻れる解に落ちるため使わない。
#   検証 (Tda 式・左足): 上 25cm / 前 15cm+上 25cm / 後 15cm+上 25cm / 前 30cm の各目標で
#   足首誤差 1〜4cm・膝は前へ屈曲。
# ===========================================================================
import math

import bpy

from . import mmd_math as mm

CONSTRAINT_NAME = "mmd2gltf_ik"
PROP_OWNER = "mmd2gltf_owner"


def remove_existing(arm):
    n = 0
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CONSTRAINT_NAME):
                pb.constraints.remove(c)
                n += 1
        if pb.get("mmd2gltf_ik_prebend"):
            pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            del pb["mmd2gltf_ik_prebend"]
        pb.use_ik_limit_x = pb.use_ik_limit_y = pb.use_ik_limit_z = False
        pb.lock_ik_x = pb.lock_ik_y = pb.lock_ik_z = False
    for o in list(bpy.data.objects):
        if o.get("mmd2gltf_role") == "ikpole" and o.get(PROP_OWNER) == arm.name:
            bpy.data.objects.remove(o, do_unlink=True)
    return n


def build(context, arm, model, apply_limits=True, report=None, prebend_deg=10.0):
    from .physics_builder import find_bone
    log = report or (lambda *a, **k: None)
    scale = model.unit_scale
    remove_existing(arm)
    n_ik = 0
    mw = arm.matrix_world
    for b in model.bones:
        ik = b.ik
        if not ik:
            continue
        links = ik.get("links") or []
        if not links:
            continue
        ik_bone = find_bone(arm, model, b.index, scale)
        end_bone = find_bone(arm, model, int(links[0]["bone"]), scale)
        if ik_bone is None or end_bone is None:
            log("WARNING", f"IK '{b.name}': ボーンが見つからずスキップ")
            continue
        pb = arm.pose.bones[end_bone.name]
        c = pb.constraints.new("IK")
        c.name = CONSTRAINT_NAME
        c.target = arm
        c.subtarget = ik_bone.name
        c.chain_count = len(links)
        c.iterations = max(1, int(ik.get("loop", 40)))
        c.use_tail = True
        c.use_stretch = False
        n_ik += 1
        if not apply_limits:
            continue

        for lk in links:
            if "min" not in lk or "max" not in lk:
                continue
            lb = find_bone(arm, model, int(lk["bone"]), scale)
            if lb is None:
                continue
            lpb = arm.pose.bones[lb.name]
            lo, hi = lk["min"], lk["max"]
            rest3 = (mw @ lb.matrix_local).to_3x3()
            limited = {}
            for pmx_axis in range(3):
                a, bmax = float(lo[pmx_axis]), float(hi[pmx_axis])
                if a == 0.0 and bmax == 0.0:
                    continue
                if a > bmax:
                    a, bmax = bmax, a
                world_axis = mm.pmx_axis_vector_to_blender([1.0 if k == pmx_axis else 0.0 for k in range(3)])
                blo, bhi = -bmax, -a                     # 鏡映で符号反転
                best, bdot = None, 0.0
                for li, name in enumerate("xyz"):
                    dd = rest3.col[li].normalized().dot(world_axis)
                    if abs(dd) > abs(bdot):
                        best, bdot = name, dd
                if best is None or abs(bdot) < 0.5:
                    continue
                if bdot < 0:
                    blo, bhi = -bhi, -blo
                blo = max(-math.pi, blo)
                bhi = min(math.pi, bhi)
                setattr(lpb, f"use_ik_limit_{best}", True)
                setattr(lpb, f"ik_min_{best}", blo)
                setattr(lpb, f"ik_max_{best}", bhi)
                limited[best] = (blo, bhi)
            if not limited:
                continue
            for other in "xyz":
                if other not in limited:
                    setattr(lpb, f"lock_ik_{other}", True)
            # 初期屈曲: 制限範囲の内側へ prebend_deg だけ倒しておく
            if prebend_deg > 0.0:
                lpb.rotation_mode = "XYZ"
                eul = [0.0, 0.0, 0.0]
                for ax, (blo, bhi) in limited.items():
                    if blo >= 0.0 - 1e-6:
                        eul["xyz".index(ax)] = math.radians(prebend_deg)
                    elif bhi <= 0.0 + 1e-6:
                        eul["xyz".index(ax)] = -math.radians(prebend_deg)
                lpb.rotation_euler = eul
                lpb["mmd2gltf_ik_prebend"] = True
    return n_ik
