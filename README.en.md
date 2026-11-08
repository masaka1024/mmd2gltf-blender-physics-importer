# mmd2gltf-blender-physics-importer

**[日本語](README.md) | English**

<!-- mmd2gltf-ecosystem:start -->
> **The mmd2gltf ecosystem** — Convert an MMD model (PMX/VMD) to `.glb` once, and bring it into **Unity, Unreal Engine 5, or Blender** as is, with physics (secondary motion) and materials (toon/sphere). MMD-specific data that glTF cannot express is kept under `extras.mmd` as the original values, and each importer reads it to rebuild the model on the engine side.
>
> ```
> PMX / VMD
>    │  convert (mmd2gltf-gui or mmd2gltf-cs)
>    ▼
> .glb + extras.mmd   ← converted only once
>    │
>    ├─▶ Unity   … mmd2gltf-unity-physics-importer
>    ├─▶ UE5     … mmd2gltf-ue5-physics-importer
>    └─▶ Blender … mmd2gltf-blender-physics-importer
> ```
>
> | Goal | Use |
> |---|---|
> | Convert first (Windows EXE available) | [mmd2gltf-gui](https://github.com/masaka1024/mmd2gltf-gui) — Python. GUI / CLI |
> | Convert (C#, with built-in physics bake) | [mmd2gltf-cs](https://github.com/masaka1024/mmd2gltf-cs) — same output format as gui; bakes secondary motion with the in-house engine |
> | Run it in Unity | [mmd2gltf-unity-physics-importer](https://github.com/masaka1024/mmd2gltf-unity-physics-importer) — Editor extension. Bundled Bullet-compatible engine (no PhysX) |
> | Run it in Unreal Engine 5 | [mmd2gltf-ue5-physics-importer](https://github.com/masaka1024/mmd2gltf-ue5-physics-importer) — C++ plugin. C++ port of the same engine (no Chaos) |
> | Run it in Blender | `mmd2gltf-blender-physics-importer` (coming soon) — Add-on. Wires up Blender's built-in Bullet |
> | (Library) the physics engine itself | [mmd2gltf-cs-physics](https://github.com/masaka1024/mmd2gltf-cs-physics) — Bullet 2.75-compatible engine used by cs / Unity / UE5. Not used directly |
> | (Separate route) export glTF/FBX from Blender's mmd_tools | [mmd-to-gltf-exporter](https://github.com/masaka1024/mmd-to-gltf-exporter) — no `extras.mmd` (physics is not rebuilt) |
>
> Secondary-motion behavior matches between Unity and UE5 because they share the same engine. The Blender version drives Blender's built-in Bullet, so the motion is close but not identical.
>
> **This repository**: The side that brings the `.glb` into **Blender**.
<!-- mmd2gltf-ecosystem:end -->

A Blender add-on that imports a `.glb` produced by `mmd2gltf-gui` and rebuilds the MMD-specific data stored in
`extras.mmd` — rigid-body physics, toon / sphere materials and IK definitions — into something that actually works
inside Blender. It is the Blender counterpart of
[mmd2gltf-unity-physics-importer](https://github.com/masaka1024/mmd2gltf-unity-physics-importer).

> **Note**: this add-on depends on the specific GLB layout written by `mmd2gltf-gui` (`extras.mmd`).
> GLBs from other exporters are imported as plain glTF; no physics or material reconstruction happens.

> This is an unofficial, independent personal project. It is not affiliated with, endorsed by or supported by
> MikuMikuDance (Yu Higuchi), PmxEditor (Kyokuhoku-P), Bullet Physics, or any model / motion authors.
> Matching MMD's behaviour is a goal, not a guarantee.

## Features

### [1] Physics

Rigid bodies and joints from PMX are driven by **Blender's built-in rigid-body simulation (Bullet)**.

| PMX | Blender mapping |
|---|---|
| Rigid body (sphere / box / capsule) | `rb_<name>` mesh + Rigid Body (SPHERE / BOX / CAPSULE) |
| Mode 0 (follow bone) | Passive + Animated, Child Of constraint on the bone |
| Mode 1 (physics) | Active; the bone gets a Copy Transforms constraint tracking the body |
| Mode 2 (physics + bone alignment) | Active; the bone gets Copy Rotation only (position comes from the parent) |
| Joint (6-DoF spring) | `joint_<name>` empty + Generic Spring constraint, limits/springs remapped |
| Collision groups / masks | Non-colliding pairs get an empty constraint with `disable_collisions` (`ncc_*`) |
| Gravity | `9.8 × unitScale` so pendulum periods match MMD (optional) |

Everything is created under the `MMD Physics` collection and can be removed with one click.

### [2] Materials

Replaces the imported Principled BSDF with an MMD-style node setup (EEVEE):
toon (per-material and shared toon01–10), sphere map (multiply / add, MatCap style),
outline (Solidify + per-material edge materials), and alpha restoration (`alphaClass` / `origTexture`).
Toon / sphere images that Blender's glTF importer does not load are extracted from the GLB directly.

### [3] IK (optional)

Rebuilds Blender IK constraints from `nodes[i].extras.mmd.ik`, with approximate angle limits. Limited links
(knees) get a small pre-bend (10° by default) in the pose so the solver does not get stuck trying to hyperextend
a perfectly straight leg (the same problem MMD solves with its knee special case).

## Requirements

- Blender 4.2 or later (developed on 5.2 LTS) with the bundled glTF importer enabled
- A `.glb` converted by `mmd2gltf-gui`
- Optional: `toon01.bmp`–`toon10.bmp` from the MMD distribution for shared toons

## Install

Build a zip with `tools/build_zip.sh` and install it from *Edit > Preferences > Extensions > Install from Disk*.

## Usage

1. *File > Import > mmd2gltf GLB (.glb)* and pick the file (physics / materials / IK toggles in the dialog)
2. The **mmd2gltf** tab in the 3D viewport sidebar (`N`) lets you run each step again later
3. Press *Reset physics cache* and play **from the start frame** (Blender's rigid bodies only simulate
   sequentially); use *Bake physics* to scrub or export. Use Material Preview / EEVEE for the toon look.

## Design notes (differences from the Unity version)

- **Initial penetration**: MMD models are authored with hair roots and skirt rows inside the body colliders.
  Blender's Bullet pushes them out, so skirts fly open on the first frames (measured: 8 cm in 3 frames).
  The add-on detects overlapping pairs in the rest pose and disables collision for them permanently
  (the Unity engine re-enables collision once separated; Blender constraints cannot be toggled while simulating).
- **Collision groups**: `collision_collections` cannot express pairwise masks, so non-colliding pairs are
  reproduced with disable-collision constraints, limited by distance (1.5 × sum of radii by default).
- **Animated models**: Blender builds the rigid-body world from the *pre-constraint* matrices of kinematic
  bodies, so they are placed at the start-frame pose with a matching Child Of inverse; dynamic bodies of a chain
  are placed by the first non-physics ancestor (FK-rest), which keeps joint frames consistent with baked hair keys.
- **Zero-size bodies** without joints are converted to bone-follow bodies with a warning.
- **Coordinates**: raw PMX values are assembled in world space and mapped as
  `(x, y, z) → (x, z, y) × unitScale` with rotations conjugated by the y/z swap (angles flip sign).
  `physicsGltf` (bone-local) is not used because Blender's imported bone axes differ from glTF node axes.
- Toon shading uses Shader to RGB and therefore works in **EEVEE only**.
- Not supported: `ambient` / `specular`, `sphereMode = 3`, bone / group / material morphs.

## Verification

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P tests/run_headless.py -- model.glb 60
```

## License

MIT License / Copyright (c) 2026 masaka1024 — see [LICENSE](LICENSE).
