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

The secondary-motion engine is chosen with the **Physics engine** switch in the panel (a per-scene setting).

| | Native engine (default) | Blender Bullet |
|---|---|---|
| What computes it | The **same in-house engine as Unity / UE5** (a C++ implementation compatible with Bullet 2.75). Blender's own physics is not used | Blender's built-in rigid-body simulation (Bullet) |
| Fidelity | The **bake is bit-identical to the C# engine run with the Unity / UE5 playback settings** (measured on macOS arm64 and on Windows x64, each against the C# engine on the same machine; the Windows x64 library was built with MSVC 14.44 and checked on one PC) | Close, but not identical |
| Wiring | None (no rigid-body / joint objects are created) | Required (a button builds rigid bodies and joints) |
| How to use | Bake / real-time display | Play the timeline from the start / bake |
| Needs | The native library (the current zip bundles macOS arm64 and Windows x64) | Nothing |

If the native library cannot be loaded (unsupported OS/CPU, missing file, ...), the native engine becomes
unselectable and the panel shows the reason together with the Blender Bullet UI.

#### Native engine

The panel has three buttons. All need a target armature and its GLB path (recorded at import; otherwise use the GLB field in the panel).

| Button | What it does |
|---|---|
| **物理をベイク (自作エンジン)** (Bake physics, native engine) | Computes the physics for every frame from the GLB's animation and writes keys for the secondary-motion bones into the NLA track **"MMD Physics Bake"**. Playback, render and export use that track |
| **ベイクを削除** (Delete bake) | Removes the "MMD Physics Bake" track and its action |
| **リアルタイム表示** (Real-time display) | Computes physics on every frame change and moves the secondary-motion bones (press again to stop) |

**Pre-baked GLBs:** if the active action already has keys on the secondary-motion bones, Blender would evaluate it above the NLA and hide the bake. While a bake exists, the add-on therefore mutes those bones' fcurves in the active action (keys are never modified). *ベイクを削除* restores them. If that action was renamed or unassigned so it cannot be found, a warning is shown; rename it back and press *ベイクを削除* again.

**Armatures already wired for Blender Bullet:** the wiring's bone constraints override the bake and the real-time display, so a warning is shown and the panel offers *物理を削除* (Remove physics) — press it first. Switching the engine to Blender Bullet stops the real-time display.

- **Bake conditions**: the Unity / UE5 playback settings (step 1/60 × 2, solver iterations 10, joint-velocity iterations 40, ...)
  plus the UE5 node's write-back settings (mode-2 alignment, rotation clamp 0.5). The procedure is the same as physbake
  (mmd2gltf-cs): align to the first-frame pose, 2 s of warm-up, then step every frame. The input is the animation inside the GLB;
  **actions you edit in Blender are not reflected in the bake.**
- **fps**: keys follow the **scene fps at bake time**. If you change the fps, delete the bake and bake again.
- **Real-time display** (it takes the pose Blender evaluated as input, so bit-exactness is not a goal):
  - Seeking or going backward resets the physics and recomputes.
  - There is no warm-up, so the secondary-motion bones are still settling during the first few frames.
  - It does nothing for an armature that already has a bake (it waits); delete the bake to enable it.
  - Stopping returns the physics bones to their rest pose.
  - If the computation diverges (NaN), the panel shows a warning.

#### Blender Bullet

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

Everything is created under the `MMD Physics` collection and can be removed with one click. See "When you choose Blender Bullet" below for the caveats specific to this route.

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
- A `.glb` converted by `mmd2gltf-gui` (or `mmd2gltf-cs`)
- For the native engine: macOS 11.2 or later (Apple Silicon); the library ships in the zip. The Windows (x64) library ships in the zip too (verified on Blender 5.2).
  Where the library is absent (Intel Mac, Linux, ...) the add-on still installs, shows the reason in the panel and uses Blender Bullet
- Optional: `toon01.bmp`–`toon10.bmp` from the MMD distribution for shared toons

## Install

Build a zip with `tools/build_zip.sh` and install it from *Edit > Preferences > Extensions > Install from Disk*.

**macOS note**: the native library (`native_lib/libmmdphys.dylib`) is ad-hoc signed and not notarized. Installing through
Blender's *Install from Disk* is fine: extraction the way Blender does it leaves no quarantine attribute (verified by simulating with Python zipfile; GUI install not yet tried). But if you extract a browser-downloaded zip with Finder and copy the
resulting **folder** into the extensions folder by hand, the quarantine attribute can remain and the library fails to load.
The panel shows the reason; run `xattr -d com.apple.quarantine <path to libmmdphys.dylib>` in a terminal, then restart Blender
(or disable and re-enable the add-on) — the load result is cached for the session.

## Usage

1. *File > Import > mmd2gltf GLB (.glb)* and pick the file (physics / materials / IK toggles in the dialog)
2. The **mmd2gltf** tab in the 3D viewport sidebar (`N`) lets you run each step again later
3. Pick the **Physics engine** (native by default).
   - **Native engine**: press *物理をベイク (自作エンジン)*. No wiring is needed (and the import dialog's "wire physics"
     creates nothing while the native engine is selected; if the library cannot load, Blender Bullet is wired instead).
     Afterwards you can play, render, export and scrub freely. *リアルタイム表示* is handy for a quick look (it only works while there is no bake).
   - **Blender Bullet**: press *Reset physics cache* and play **from the start frame** (Blender's rigid bodies only
     simulate sequentially); use *Bake physics* to scrub or export.
4. Use Material Preview / EEVEE for the toon look.

## Design notes (differences from the Unity version)

### Common

- **Coordinates**: raw PMX values are assembled in world space and mapped as
  `(x, y, z) → (x, z, y) × unitScale` with rotations conjugated by the y/z swap (angles flip sign).
  `physicsGltf` (bone-local) is not used because Blender's imported bone axes differ from glTF node axes.
- Toon shading uses Shader to RGB and therefore works in **EEVEE only**.
- Not supported: `ambient` / `specular`, `sphereMode = 3`, bone / group / material morphs.

### When you choose Blender Bullet

The native engine is not affected by these (the same engine as Unity / UE5 computes with the same settings).

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

## Known limitations

- **Native engine: physics → non-physics → physics bone chains** are not exact (the middle bone is handled with FK).
- **Blender 4.2–4.3** (legacy action API path): the native bake is untested there (verified on 5.1 / 5.2).
- **Windows library** has been verified on one PC only (Windows x64 / MSVC 14.44 / Blender 5.2). Other CPUs and Blender versions are untested.
- **Blender 5.1's own glTF importer** can crash on some MMD GLBs that carry custom attributes (`_SDEF_*`, `_EDGESCALE`, `_WEIGHTTYPE`). That is an importer bug, not this add-on.

## Building the native library

The native engine library is built from `native/` (CMake). The distributed zip already contains a prebuilt one, so this is only needed for development.

```bash
cp native/tests/local_env.example.sh native/tests/local_env.sh   # edit the paths (not committed)
source native/tests/local_env.sh
cmake -S native -B native/build -DCMAKE_BUILD_TYPE=Release
cmake --build native/build
ctest --test-dir native/build
cmake --install native/build     # copies into mmd2gltf_blender_importer/native_lib/ (macOS also ad-hoc signs)
```

- Build in **Release** and never use `-ffast-math` / `-Ofast` / `/fp:fast` (results would drift from the C# engine; CMake sets `-ffp-contract=off` / `/fp:precise`).
- `ctest` includes the bit-exact parity tests against the C# reference (layers 2-4). Put the sibling repositories next to this one:
  `mmd2gltf-ue5-physics-importer` (sync check of `native/vendor/MmdPhysicsCore`) and `mmd2gltf-cs` (physbake produces the reference CSVs; needs the .NET SDK).
  If they live elsewhere set `MMD_UE5_REPO` / `MMD_CS_REPO` and `-DMMD_UE5_CORE_DIR=...`.
  Test GLBs are passed via environment variables set before configuring (never committed; see `native/tests/local_env.example.sh`).
  A test whose variable is unset fails with a message saying so.

  | Variable | Contents | Used by |
  |---|---|---|
  | `MMD_TEST_GLB_SMALL` | small animated GLB | test_load / parity_engine / *_small / capi, headless tests |
  | `MMD_TEST_GLB` | large animated GLB | *_big |
  | `MMD_TEST_GLB_NOEXTRAS` | GLB without extras.mmd | capi |
  | `MMD_TEST_GLB_NOANIM` | GLB without animation | capi |
  | `MMD_TEST_GLB_PREBAKED` | GLB with physics already baked into its keys | `tests/headless_selfphys_override.py` |
- The minimum macOS is 11.2 (`CMAKE_OSX_DEPLOYMENT_TARGET`, matching Blender 4.2). For an existing build directory, recreate it or pass `-DCMAKE_OSX_DEPLOYMENT_TARGET=11.2`.
- On Windows build with Visual Studio (MSVC); `bash` (Git for Windows) is required.
- To use a library you built elsewhere, set the environment variable `MMD_NATIVE_LIB` to its full path.

## Layout

```
mmd2gltf_blender_importer/
  __init__.py          operators, panel, settings
  engine_bridge.py     ctypes wrapper for the native engine
  selfphys_bake.py     native bake -> NLA track "MMD Physics Bake"
  selfphys_live.py     native real-time display (frame-change handler)
  native_lib/          prebuilt library (libmmdphys.dylib / mmdphys.dll)
  physics_builder.py   Blender Bullet route: rigid bodies / joints / non-collision constraints
  glb_reader.py, mmd_math.py, overlap.py, material_builder.py, ik_builder.py
native/                C++ sources of the in-house engine (CMake)
  vendor/MmdPhysicsCore/  UE5 engine, vendored unmodified
  shim/  UE-type replacement headers
  src/   GLB reader, FK, bake, C API
  tests/ bit-exact tests against the C# reference (CTest)
tests/run_headless.py  headless check
tools/build_zip.sh     builds the distribution zip
```

## Verification

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P tests/run_headless.py -- model.glb 60
```

The native-engine parts are checked by `tests/headless_*.py` (pass the GLB as an argument or via the variables above).

## License

MIT License / Copyright (c) 2026 masaka1024 — see [LICENSE](LICENSE).
