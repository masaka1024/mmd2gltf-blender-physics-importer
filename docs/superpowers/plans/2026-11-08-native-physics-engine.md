# 自作物理エンジン (C++) 組み込み 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Blender アドオンの揺れ物を、Unity / UE5 と同じ自作エンジン (UE5 版 `MmdPhysicsCore` の C++) で計算できるようにし、ベイク結果を C# エンジンの再生設定とビット一致させる。

**Architecture:** UE5 版のソースを無改変で `native/vendor/` に取り込み、UE 型の互換ヘッダでビルドした共有ライブラリを `ctypes` で呼ぶ。FK とベイク手順は C# (physbake) から 1:1 で C++ に移植し、比較用の C# 基準は physbake にオプションを足して作る。既存の Blender Bullet 方式は切り替え式で残す。

**Tech Stack:** C++17 / CMake 3.20+ / CTest、Python 3.11 (Blender 内蔵) + ctypes + mathutils、C# (.NET 10, mmd2gltf-cs の physbake)、Blender 4.2+ (検証は 5.2.1)。

**Spec:** `docs/superpowers/specs/2026-11-08-native-physics-engine-design.md`

## Global Constraints

- `native/vendor/MmdPhysicsCore/` は UE5 版の**無改変コピー**。編集禁止。差分は互換層 (`native/shim/`) 側で吸収する。
- 取り込み元: `../mmd2gltf-ue5-physics-importer/Plugins/MmdPhysicsImporter/Source/MmdPhysicsCore/{Public,Private}`。除外: `Private/Tests/`、`Private/MmdPhysicsCoreModule.cpp`、`MmdGlbMaterialReader.*`、`MmdScopedUtf8CType.*`。
- 浮動小数点: clang `-ffp-contract=off`、MSVC `/fp:precise`。`-ffast-math` / `-Ofast` / `/fp:fast` は禁止。
- 互換層の `FMath` は標準ライブラリの同名関数をそのまま呼ぶ (`FMath::Sqrt(float)` → `std::sqrt(float)` など)。別の近似や式変形をしない。
- `TCHAR` は `char16_t`、`TEXT(x)` は `u##x`、`FString` は `std::u16string` を包む (UE の Mac/Windows と同じ 16 ビット文字)。
- C# と C++ の比較用 CSV の数値は **IEEE-754 float32 のビット列を 8 桁の大文字 16 進**で書く (C#: `BitConverter.SingleToInt32Bits(v).ToString("X8")`、C++: `memcpy` → `%08X`)。テキストの一致 = ビット一致。
- 姿勢 1 個 = float 7 個 `px,py,pz,qx,qy,qz,qw`、**raw PMX 座標** (左手系 Y-up、未スケール)。
- 再生設定 (C# と C++ で同じ値): `FixedTimeStep 1/60`、`SubSteps 2`、`SolverIterations 10`、`JointVelocityIterations 40`、`JointMaxCorrectionVel 30`、`UseSplitImpulse true`、`UseJointSplitImpulse true`、mode2 位置合わせ ON、`AlignBonePositions false`、`AlignRotClampAlpha 0.5`、空回し 2.0 秒。重力はコア既定 (98) のまま。
- ベイク入力は GLB の `animations[0]` のみ。
- テスト素材は環境変数で渡す: `MMD_TEST_GLB` (既定 `~/Downloads/IA 2/IA.glb`)、`MMD_TEST_GLB_SMALL` (既定 `~/Desktop/IA_Conqueror_v4のコピー.glb`)。リポジトリに GLB を入れない。
- Blender 実行ファイル: `/Applications/Blender.app/Contents/MacOS/Blender`。
- 対応プラットフォーム: `macos-arm64`、`windows-x64`。
- コミットは各タスクの最後。このリポジトリには push しない (公開は別作業)。mmd2gltf-cs へのコミットは作業ブランチ `feat/physbake-playback` に置き、push しない。

## Review Focus

1. **日本語を含む GLB パス** — `IA_Conqueror_v4のコピー.glb` のような名前でも、C 窓口が UTF-8 で受けて開ける (Task 7 にテスト)。
2. **シーンの fps が GLB のアニメと違う** (例: シーン 24fps) — ベイクのキーは、Blender の glTF インポーターが元のアクションに打ったのと同じフレーム位置に乗る (Task 9 にテスト)。
3. **焼き直し** — 2 回ベイクしても NLA の段は 1 本だけで、中身が新しい結果に置き換わる (Task 9 にテスト)。
4. **揺れ物ボーンの親も揺れ物ボーン** (鎖) — 子ボーンのローカル姿勢は親の**物理結果**を基準に計算され、FK の親を基準にしない (Task 9 の全揺れ物ボーン一致テストで検出)。
5. **リアルタイム中にファイルを開き直す / アドオンを無効化する** — 例外を出さずにハンドラとネイティブのハンドルが解放される (Task 10 にテスト)。

---

### Task 0: リポジトリを git 管理にする

**Files:**
- Create: `.gitignore` (既存を拡張: `native/build/`、`__pycache__/`、`*.pyc`、`dist/`)

- [ ] **Step 1:** `git init -b main` → 既存ファイルを `git add` → `git commit -m "chore: 既存のアドオンを git 管理に入れる"`。
- [ ] **Step 2:** `git status` が clean であることを確認する。

---

### Task 1: 試作 — フレーム変更ハンドラでのポーズ書き込み (捨てるコード)

仕様 §5.2 の危険箇所を最初に潰す。結論だけを仕様書 §9 に書き、試作コードは残さない。

**Files:**
- Create (一時): `tests/spikes/live_write_spike.py` (結論を書いたら削除)
- Modify: `docs/superpowers/specs/2026-11-08-native-physics-engine-design.md` §9

- [ ] **Step 1:** 試作スクリプトを書く: `MMD_TEST_GLB_SMALL` を glTF インポーターで読み込み、`frame_change_post` ハンドラで揺れ物ボーン 1 本の `pose_bone.matrix_basis` に「フレーム番号 × 1°」の回転を書く。フレーム 1〜10 を `scene.frame_set` で進めて、各フレームで (a) `depsgraph` で評価したボーン行列と (b) `bpy.ops.render.render(write_still=True)` 相当のレンダー評価 (`object.evaluated_get(depsgraph).pose`) がそのフレームの値かを記録する。
- [ ] **Step 2:** `Blender -b -P tests/spikes/live_write_spike.py` で実行する。期待: どちらも同じフレームの値 (遅れ 0)。
- [ ] **Step 3:** 遅れがある場合は `frame_change_pre` で前フレームの結果を書く方式と、`depsgraph_update_post` 方式を同じ手順で試し、遅れ 0 の方式を選ぶ。
- [ ] **Step 4:** 採用した方式・試した方式・計測値を仕様書 §9 に書き、試作スクリプトを削除してコミット (`docs: リアルタイム書き込み方式の試作結果`)。

---

### Task 2: ネイティブの骨組み — vendor 取り込み・同期検査・互換層

**Files:**
- Create: `native/CMakeLists.txt`、`native/tools/import_vendor.py`、`native/tools/sync_check.py`、`native/shim/` 一式、`native/tests/test_load.cpp`
- Create (コピー): `native/vendor/MmdPhysicsCore/{Public,Private}/…`

**Interfaces:**
- Produces: CMake ターゲット `mmdphys_core` (静的ライブラリ、vendor + shim)。`native/tools/sync_check.py <ue5_core_dir> <vendor_dir>` → 一致で終了コード 0、不一致でファイル名を出して 1、取り込み元が無ければ警告して 0。
- Produces: CMake オプション `MMD_UE5_CORE_DIR` (既定 `${CMAKE_SOURCE_DIR}/../../mmd2gltf-ue5-physics-importer/Plugins/MmdPhysicsImporter/Source/MmdPhysicsCore`)。

- [ ] **Step 1: 同期検査のテスト** — `native/tests/test_sync_check.py` に 3 ケース: 同一ファイル → 0、コメントと空白だけ違う → 0、コード 1 文字違う → 1。正規化は「`//` 行コメント・`/* */` ブロックコメント・空白・BOM を除去して SHA-256」(C# SyncGuard と同じ規則)。
- [ ] **Step 2:** `python3 native/tests/test_sync_check.py` → FAIL (スクリプトが無い)。
- [ ] **Step 3:** `sync_check.py` と `import_vendor.py` (Global Constraints の除外規則でコピーする) を実装し、vendor を取り込む。CMake の configure 段階で `sync_check.py` を実行し、1 なら `FATAL_ERROR`。`CMakeLists.txt` は C++17、全ターゲットに浮動小数点フラグ (clang `-ffp-contract=off`、MSVC `/fp:precise`) を付け、`CMAKE_BUILD_TYPE` 既定を `Release` (`-O2`、`-ffast-math` 無し) にする。
- [ ] **Step 4:** `python3 native/tests/test_sync_check.py` → PASS。
- [ ] **Step 5: 読み込みテスト** — `test_load.cpp`: `MmdPhysics::GlbPhysicsReader` で `MMD_TEST_GLB_SMALL` を読み、剛体 117・ジョイント 165・ボーン 179 を確認 (Task 前の実測値)。`PmxPhysicsBuilder::Build` が null でないこと。
- [ ] **Step 6:** `cmake -S native -B native/build && cmake --build native/build && ctest --test-dir native/build -R load` → まずコンパイルエラーになる。足りない UE 型を互換層に足していく。互換層が再現するもの (計数済み): `TArray` (`Num/Add/SetNum/SetNumZeroed/Reset/RemoveAt/Init/Reserve/Contains/IsValidIndex/GetData/Sort/Last`)、`TMap`、`TSet`、`TPair`、`TOptional` (`IsSet/GetValue`)、`TSharedPtr`/`MakeShared`、`TFunctionRef`、`TNumericLimits<float>`、`FString` (`Printf/Len/Mid/IsEmpty/Equals/Contains/Find/AppendChar/operator*`)、`FCString::Atod/Strtoi` (ロケール非依存: `strtod_l` / `_strtod_l` の "C" ロケール)、`FUTF8ToTCHAR`、`FFileHelper::LoadFileToArray` (Windows は `_wfopen`)、`FMemory::Memcpy`、`FPlatformTime::Seconds`、`FMath::{Abs,Acos,Asin,Atan,Clamp,Cos,Max,Min,Pow,RoundHalfToEven,Sin,Sqrt}`、`UE_LOG`/`DECLARE_LOG_CATEGORY_EXTERN`/`DEFINE_LOG_CATEGORY(_STATIC)` (何もしない)、`FORCEINLINE`、`INDEX_NONE`、`PLATFORM_WINDOWS`/`PLATFORM_MAC`、`MMDPHYSICSCORE_API` (空)、`int32/int64/uint8/ANSICHAR`。
- [ ] **Step 7:** ビルドが通り `ctest -R load` → PASS。`git diff --stat native/vendor` が取り込み直後から変化していないこと。
- [ ] **Step 8:** コミット (`feat(native): vendor 取り込みと UE 互換層`)。

---

### Task 3: エンジン単体のビット一致 (検証の層 2)

**Files:**
- Create: `native/tests/parity_engine.cpp`、`native/tests/csv_bits.h` (CSV 読み書きの共通処理)

**Interfaces:**
- Consumes: `mmdphys_core` (Task 2)。
- Produces: `csv_bits.h` の `bool ParseRoundTripFloat(const std::string&, float&)` と `std::string Hex8(float)` (Task 5・6 も使う)。

- [ ] **Step 1: 基準を作る** — `cd ../mmd2gltf-ue5-physics-importer && dotnet run --project Tools/CsReference -- "$MMD_TEST_GLB_SMALL" 300 /tmp/…/ref_engine.csv --per-frame --drive --playback` (出力先はビルドディレクトリ)。CMake のテスト定義から呼べるように `native/tests/make_ref_engine.sh` にする。
- [ ] **Step 2: テストを書く** — `parity_engine.cpp`: UE5 版 `MmdPhysicsGlbParityTest.cpp` の手順をそのまま (互換層で) 再現する。`ApplyDrive` は同ファイルの式をコピーし、`ApplyPlaybackSettings` は CsReference と同じ 7 項目。300 フレームの全剛体の位置・回転を基準 CSV (`"R"` 形式の float) と比較し、**1 ビットでも違えば** 最初にずれたフレーム・剛体名・値を出して FAIL。
- [ ] **Step 3:** `ctest --test-dir native/build -R parity_engine` → PASS (最大差 0)。FAIL のときは互換層の `FMath`・`FCString::Atod`・浮動小数点フラグを疑う (vendor は触らない)。
- [ ] **Step 4:** コミット (`test(native): エンジン単体のビット一致`)。

---

### Task 4: C# 基準 — physbake に `--playback` と CSV 出力を足す (mmd2gltf-cs)

**Files (mmd2gltf-cs リポジトリ、ブランチ `feat/physbake-playback`):**
- Modify: `src/Mmd2Gltf.Core/Physics/PhysicsBaker.cs` (`BakeOptions` に項目追加、`Run` で適用)
- Modify: `src/Mmd2Gltf.PhysBake/Program.cs` (オプション 3 つ)
- Possibly modify: `external/mmd-physics` (サブモジュールの版合わせ。Step 1 の結果次第)

**Interfaces:**
- Produces: `physbake <glb> --playback --dump-fk <csv> --dump-bake <csv> --dry-run`
  - `--dump-fk`: ヘッダ `frame,bone,px,py,pz,qx,qy,qz,qw`、全フレーム × 全ボーンの FK 結果 (`fkWorld`)、数値は Hex8。
  - `--dump-bake`: 同じヘッダ、全フレーム × 全ボーンの最終 world (`fkWorldRaw` を物理結果で上書きした後)、数値は Hex8。
- Produces: `BakeOptions` の新項目 `JointVelocityIterations` (int, 0=エンジン既定)、`JointMaxCorrectionVel` (float, 0=既定)、`UseSplitImpulse` (bool?)、`UseJointSplitImpulse` (bool?)、`AlignRotClampAlpha` (float, 既定 0 = 従来どおり)。`--playback` はこれらと `SimHz=60`・`SubSteps=2`・`SolverIterations=10`・`AlignRotClampAlpha=0.5` を設定する。

- [ ] **Step 1: エンジンの版を揃える** — `external/mmd-physics` の `Assets/MmdPhysics/{Core,Pmx}` と、UE5 版が移植元にしている版 (`../mmd2gltf-ue5-physics-importer/docs/porting_notes.md` の記載、無ければ `../mmd2gltf-cs-physics` の現在の main) を、Task 2 と同じ正規化ハッシュで比較する。違えばサブモジュールをその版に更新し、既存のテスト (`dotnet test`、あれば) を通す。どの版に揃えたかをコミットメッセージに書く。
- [ ] **Step 2: 既存の動作が変わらないことの基準を取る** — 変更前に `physbake "$MMD_TEST_GLB_SMALL" -o before.glb` を実行し、SHA-256 を控える。
- [ ] **Step 3:** オプションと `BakeOptions` の項目を実装する。`PhysicsBaker.Run` は新項目が既定値のときに従来と同じコードを通ること。`ComputeAlignedBonePoses(DrivenAt(f), opt.AlignRotClampAlpha, opt.AlignBonePositions)` に変える。
- [ ] **Step 4:** `physbake "$MMD_TEST_GLB_SMALL" -o after.glb` の SHA-256 が Step 2 と同じ。
- [ ] **Step 5:** `physbake "$MMD_TEST_GLB_SMALL" --playback --dump-fk fk.csv --dump-bake bake.csv --dry-run` が成功し、行数 = フレーム数 × 179 + 1。
- [ ] **Step 6:** コミット (`feat(physbake): --playback と FK/ベイクの CSV 出力`)。

---

### Task 5: FK の C++ 移植 (検証の層 3)

**Files:**
- Create: `native/src/mmd_glb.h/.cpp` (GLB コンテナと accessor 読み出し。JSON は vendor の `MmdMiniJson` を使う)
- Create: `native/src/mmd_fk.h/.cpp`
- Create: `native/tests/parity_fk.cpp`

**Interfaces:**
- Consumes: vendor の `MmdPhysics::{Vec3, Quat, RigidTransform}`、`MmdMiniJson`。
- Produces (namespace `mmdnative`):
  - `struct GlbFile { std::shared_ptr<FMmdJsonObject> Json; std::vector<uint8_t> Bin; }` / `bool LoadGlb(const std::vector<uint8_t>& bytes, GlbFile& out, std::string& err)`
  - `struct Skeleton { int BoneCount; std::vector<int> NodeOfBone, ParentBone; std::vector<Vec3> RestPos; std::vector<Quat> RestRot; std::vector<int> TopologicalOrder() const; }` / `bool LoadSkeleton(const GlbFile&, Skeleton&, std::string& err)` — 移植元 `GltfSkeleton.Load`
  - `struct Animation { std::string Name; std::vector<float> Times; void SampleLocal(int node, float t, const Vec3& restPos, const Quat& restRot, Vec3& outPos, Quat& outRot) const; }` / `bool LoadAnimation(const GlbFile&, int index, Animation&, std::string& err)` — 移植元 `GltfAnimation.Load` / `SampleLocal`。`CUBICSPLINE` はエラー。
  - `RigidTransform GltfToMmd(const Vec3& pos, const Quat& rot, float unitScale)` — 移植元 `MmdConvert.GltfToMmd`
  - `std::vector<std::vector<RigidTransform>> ComputeFk(const Skeleton&, const Animation&, float unitScale)` — physbake `Program.cs` の FK ループ (行 140〜160 付近)
  - `float CheckBindPose(const Skeleton&, const PmxPhysicsModel&, float unitScale)` — physbake の検算 (最大誤差を返す)

- [ ] **Step 1: テストを書く** — `parity_fk.cpp`: `MMD_TEST_GLB_SMALL` で `ComputeFk` を回し、Task 4 の `fk.csv` と全行を Hex8 で比較。`CheckBindPose` < 1e-2。
- [ ] **Step 2:** `ctest -R parity_fk` → FAIL (未実装)。
- [ ] **Step 3:** 移植する。各関数の先頭に移植元のファイル名と行範囲をコメントで書く。式の順序・型 (float / double) を移植元から変えない。
- [ ] **Step 4:** `ctest -R parity_fk` → PASS。`MMD_TEST_GLB` (大きい方) でも PASS。
- [ ] **Step 5:** コミット (`feat(native): FK を C# から移植`)。

---

### Task 6: ベイクの C++ 移植 (検証の層 4)

**Files:**
- Create: `native/src/mmd_bake.h/.cpp`、`native/tests/parity_bake.cpp`

**Interfaces:**
- Consumes: Task 5 の `ComputeFk` ほか、vendor の `PmxPhysicsBuilder`。
- Produces (namespace `mmdnative`):
  - `struct PlaybackSettings { float FixedTimeStep = 1.0f/60.0f; int SubSteps = 2; int SolverIterations = 10; int JointVelocityIterations = 40; float JointMaxCorrectionVel = 30.0f; bool UseSplitImpulse = true; bool UseJointSplitImpulse = true; bool BoneMergeMode = true; bool AlignBonePositions = false; float AlignRotClampAlpha = 0.5f; float WarmupSeconds = 2.0f; }`
  - `void ApplySolverSettings(PhysicsWorld&, const PlaybackSettings&)`
  - `struct BakeResult { std::vector<int> PhysicsBones; std::vector<std::vector<RigidTransform>> BoneWorld; /* 全ボーン。物理ボーンを上書き済み */ std::string NanBone; }`
  - `BakeResult RunBake(const std::shared_ptr<PmxPhysicsModel>&, const std::vector<std::vector<RigidTransform>>& fk, const std::vector<float>& times, const PlaybackSettings&, const std::function<void(int,int)>& progress)` — 移植元 `PhysicsBaker.Run` + physbake の上書き処理

- [ ] **Step 1: テストを書く** — `parity_bake.cpp`: `RunBake` の `BoneWorld` を Task 4 の `bake.csv` と全行 Hex8 で比較。`PhysicsBones` が空でないこと、`NanBone` が空であること。
- [ ] **Step 2:** `ctest -R parity_bake` → FAIL。
- [ ] **Step 3:** 移植する (ウォームアップのステップ数 = `round(WarmupSeconds / FixedTimeStep)`、NaN ガード、mode2 判定は移植元どおり)。
- [ ] **Step 4:** `ctest -R parity_bake` → PASS (小・大の両方)。
- [ ] **Step 5:** コミット (`feat(native): ベイクを C# から移植`)。

---

### Task 7: C 言語の窓口と共有ライブラリ

**Files:**
- Create: `native/include/mmdphys_capi.h`、`native/src/mmd_capi.cpp`、`native/tests/test_capi.cpp`
- Modify: `native/CMakeLists.txt` (共有ライブラリ `mmdphys` → `libmmdphys.dylib` / `mmdphys.dll`、`install` で `mmd2gltf_blender_importer/native_lib/` へコピー、macOS は `codesign -s - --force`)

**Interfaces (`mmdphys_capi.h`、全関数 `extern "C"`):**
```c
#define MMD_API_VERSION 1
enum { MMD_OK = 0, MMD_WARN_NAN = 1, MMD_ERR = -1 };
typedef struct MmdHandle MmdHandle;
typedef struct { int32_t bone_count, physics_bone_count, frame_count; float unit_scale; } MmdInfo;
typedef void (*MmdProgressFn)(int32_t done, int32_t total, void* user);

int32_t     mmd_api_version(void);
const char* mmd_last_error(void);                       /* UTF-8、スレッドごと */
MmdHandle*  mmd_create(const char* utf8_glb_path);      /* 失敗で NULL */
void        mmd_destroy(MmdHandle*);
int32_t     mmd_get_info(MmdHandle*, MmdInfo* out);     /* frame_count はアニメ 0 本なら 0 */
int32_t     mmd_get_physics_bones(MmdHandle*, int32_t* out, int32_t cap);
int32_t     mmd_get_rest_world(MmdHandle*, float* out, int32_t cap_bones);           /* [bone][7] */
int32_t     mmd_get_times(MmdHandle*, float* out, int32_t cap_frames);
int32_t     mmd_bake(MmdHandle*, float* out_world, int64_t cap_floats,
                     MmdProgressFn progress, void* user);                           /* [frame][bone][7] */
int32_t     mmd_live_reset(MmdHandle*, const float* bone_world_in);                 /* [bone][7] */
int32_t     mmd_live_step(MmdHandle*, const float* bone_world_in, float dt, float* bone_world_out);
```
- 全関数は内部で `try { … } catch (...)` し、例外を外に出さない。容量不足は `MMD_ERR` + メッセージ。
- `mmd_bake` は `MMD_WARN_NAN` のとき `mmd_last_error()` に最初に発散したボーン名を入れる。
- `mmd_live_*` は `PlaybackSettings` のソルバ設定で動き、`mmd_live_step` は `ApplyKinematicTargets` → `StepSimulation(dt)` → 揺れ物ボーンの姿勢 (mode2 は `ComputeAlignedBonePoses`) を返す。揺れ物以外のボーンは入力をそのまま写す。

- [ ] **Step 1: テストを書く** — `test_capi.cpp`:
  - `mmd_api_version() == 1`
  - 存在しないパス → NULL かつ `mmd_last_error()` が空でない
  - `extras.mmd` の無い GLB (`../toyota-8fd25/out/toyota_8fd25.glb`) → NULL、メッセージに `extras.mmd`
  - **日本語パス** `MMD_TEST_GLB_SMALL` (`…コピー.glb`) → 非 NULL、`bone_count == 179`
  - アニメの無い GLB (`../zatsu_anime/src_f8/work/out.glb`、`extras.mmd` あり・アニメ 0 本) → `mmd_create` は成功、`frame_count == 0`、`mmd_bake` は `MMD_ERR` でメッセージに `animation`
  - `mmd_bake` の出力が Task 6 の `RunBake` と全要素一致 (同じプロセス内で比較)
  - 容量不足のバッファ → `MMD_ERR`
  - `mmd_live_reset` → `mmd_live_step` を 60 回 (入力は FK のフレーム 0..59) → NaN 無し
- [ ] **Step 2:** `ctest -R capi` → FAIL。
- [ ] **Step 3:** 実装する。
- [ ] **Step 4:** `ctest -R capi` → PASS。`cmake --install native/build` で `native_lib/libmmdphys.dylib` が置かれ、`codesign -v` が通る。
- [ ] **Step 5:** コミット (`feat(native): C 窓口と共有ライブラリ`)。ビルド済みバイナリもコミットする (zip 作成に使うため)。

---

### Task 8: Python の窓口 `engine_bridge.py` と読み込み失敗時の扱い

**Files:**
- Create: `mmd2gltf_blender_importer/engine_bridge.py`、`tests/headless_bridge.py`

**Interfaces:**
- Produces:
  - `EXPECTED_API_VERSION = 1`
  - `def load_status() -> tuple[bool, str]` — (読み込めたか, 理由)。結果はモジュール内でキャッシュ。理由の例: `"ライブラリが見つかりません: <path>"`、`"CPU の種類が違います"`、`"版数が違います (lib 2 / addon 1)"`。
  - `class NativeModel` — `__init__(path: str)` (失敗で `NativeError`)、`info -> dict`、`physics_bones -> list[int]`、`rest_world() -> list[tuple7]`、`times() -> list[float]`、`bake(progress=None) -> (array('f'), warning: str | None)`、`live_reset(world_in: array('f'))`、`live_step(world_in, dt) -> array('f')`、`close()` (二重呼び出し可)。
  - `class NativeError(Exception)`
- 結果バッファは Python 側で `array('f')` / `(ctypes.c_float * n)` を確保して渡す。

- [ ] **Step 1: テストを書く** — `tests/headless_bridge.py` (Blender の `-b -P` で実行し、結果を JSON で出す。既存 `run_headless.py` と同じ形式):
  - 通常: `load_status()[0] is True`、`NativeModel(MMD_TEST_GLB_SMALL).info["bone_count"] == 179`
  - 環境変数 `MMD_NATIVE_LIB` で存在しないパスを指す → `load_status() == (False, "ライブラリが見つかりません: …")`
  - 版数違い: テスト用に `EXPECTED_API_VERSION` を 2 に差し替えて再読み込み → `False`、理由に `版数が違います`
  - `close()` を 2 回呼んでも例外が出ない
- [ ] **Step 2:** 実行 → FAIL。
- [ ] **Step 3:** 実装する。ライブラリの場所は `MMD_NATIVE_LIB` 環境変数 → `native_lib/` の順に探す。
- [ ] **Step 4:** 実行 → PASS。
- [ ] **Step 5:** コミット (`feat: ctypes でネイティブエンジンを呼ぶ窓口`)。

---

### Task 9: ベイク結果を Blender のアクションに書く (検証の層 5)

**Files:**
- Create: `mmd2gltf_blender_importer/selfphys_bake.py`、`tests/headless_selfphys_bake.py`

**Interfaces:**
- Consumes: `engine_bridge.NativeModel`、`mmd_math.P3` / `pmx_pos_to_blender`、`physics_builder.find_bone`。
- Produces:
  - `BAKE_ACTION_NAME = "MMD Physics Bake"`、`BAKE_TRACK_NAME = "MMD Physics Bake"`
  - `def pmx_world_to_pose_matrix(world7, rest7, bone, scale) -> Matrix` — アーマチュア空間のポーズ行列。回転は `P · (R_world · R_rest⁻¹) · P · bone.matrix_local(回転)`、位置は `pmx_pos_to_blender`。
  - `def bake(context, arm, glb_path, report) -> dict` — `{"frames", "bones", "skipped_bones", "nan_bone"}`
  - `def remove_bake(arm) -> bool`
  - `def has_bake(arm) -> bool`
- キーの位置: 時刻 `t` → フレーム `t * scene.render.fps / scene.render.fps_base` (Blender glTF インポーターと同じ対応)。
- ローカル姿勢: `matrix_basis = rest⁻¹ · parent_rest · parent_pose⁻¹ · pose` (親が無ければ `rest⁻¹ · pose`)。`parent_pose` は**同じフレームの native 出力 (全ボーン)** から作る。
- 書き込み先: 新規アクション `BAKE_ACTION_NAME` に揺れ物ボーンだけ `location` / `rotation_quaternion` のキー。NLA トラック `BAKE_TRACK_NAME` を最上段に置き、ストリップの blend は `REPLACE`。既にあれば消してから作る。

- [ ] **Step 1: テストを書く** — `tests/headless_selfphys_bake.py`:
  - `MMD_TEST_GLB_SMALL` を読み込み → `bake()` → 各揺れ物ボーン・全フレームで、評価後のボーンのワールド位置と native 出力を変換した位置の差 < 1e-4
  - NLA トラック `MMD Physics Bake` が 1 本あり、元のアクションのキー数が変わっていない
  - **焼き直し**: もう一度 `bake()` → トラックは 1 本のまま
  - **fps 違い**: `scene.render.fps = 24` にしてから `bake()` → 揺れ物でないボーン 1 本について、元アクションのキー位置 (フレーム値) の集合とベイク結果のキー位置の集合が、同じ時刻同士で一致 (差 < 1e-4 フレーム)
  - `remove_bake()` → トラックとアクションが消え、`has_bake()` が False
- [ ] **Step 2:** 実行 → FAIL。
- [ ] **Step 3:** 実装する。キーの書き込みは `fcurve.keyframe_points.foreach_set` でまとめて行う (6000 フレームでも数秒)。
- [ ] **Step 4:** 実行 → PASS。
- [ ] **Step 5:** コミット (`feat: 自作エンジンのベイクを NLA に書く`)。

---

### Task 10: リアルタイム表示 (検証の層 7)

**Files:**
- Create: `mmd2gltf_blender_importer/selfphys_live.py`、`tests/headless_selfphys_live.py`

**Interfaces:**
- Consumes: Task 1 で決めた書き込み方式、`engine_bridge.NativeModel`、`selfphys_bake.pmx_world_to_pose_matrix` と逆変換。
- Produces:
  - `def start(context, arm, glb_path) -> None` / `def stop() -> None` / `def is_running() -> bool`
  - `def blender_pose_to_pmx_world(pose_matrix, rest7, bone, scale) -> tuple7` (`pmx_world_to_pose_matrix` の逆)
  - ハンドラは `@persistent`。状態 (ハンドル・直前フレーム・アーマチュア名) はモジュール変数に持つ。
- 動作: 直前フレーム +1 のときだけ `live_step(dt = fps_base / fps)`、それ以外は `live_reset`。`selfphys_bake.has_bake(arm)` が真なら何もしない。例外は 1 回だけ警告して `stop()`。`load_pre` ハンドラとアドオンの `unregister()` で `stop()`。

- [ ] **Step 1: テストを書く** — `tests/headless_selfphys_live.py`:
  - `blender_pose_to_pmx_world(pmx_world_to_pose_matrix(x)) ≈ x` (全ボーンの静止姿勢で、差 < 1e-5)
  - `start()` → フレーム 1..60 を順に `frame_set` → 例外なし・揺れ物ボーンの行列に NaN 無し・静止姿勢から動いたボーンが 1 本以上
  - フレーム 30 → 10 へ飛ぶとリセットが呼ばれる (呼び出し回数を数える)
  - ベイク済みのアーマチュアでは計算しない (ボーン行列が元アクションのまま)
  - **開き直し**: 実行中に `bpy.ops.wm.read_factory_settings(use_empty=True)` → 例外なし、`is_running()` が False
  - 同じフレーム範囲でベイク結果との最大差を出力する (合否には使わない)
- [ ] **Step 2:** 実行 → FAIL。
- [ ] **Step 3:** 実装する。
- [ ] **Step 4:** 実行 → PASS。
- [ ] **Step 5:** コミット (`feat: 自作エンジンのリアルタイム表示`)。

---

### Task 11: パネル・オペレータ・切り替え・配布 (検証の層 6)

**Files:**
- Modify: `mmd2gltf_blender_importer/__init__.py` (設定・オペレータ・パネル・`unregister`)
- Modify: `mmd2gltf_blender_importer/blender_manifest.toml` (`platforms = ["macos-arm64", "windows-x64"]`、`version = "0.2.0"`)
- Modify: `tools/build_zip.sh` (`native_lib/` を含める)
- Modify: `tests/run_headless.py` (エンジン指定の引数を追加)

**Interfaces:**
- Produces:
  - シーン設定 `engine: EnumProperty(items=[("NATIVE","自作エンジン",…),("BULLET","Blender Bullet",…)], default="NATIVE")`
  - オペレータ `mmd2gltf.native_bake` (「物理をベイク (自作エンジン)」)、`mmd2gltf.native_remove_bake` (「ベイクを削除」)、`mmd2gltf.native_live_toggle` (「リアルタイム表示」)
  - 読み込みオペレータの「物理を配線」は `engine` が NATIVE なら何もしない (自作エンジンは配線が要らない)。BULLET なら従来どおり。
  - `mmd2gltf.native_bake` は `window_manager.progress_begin/update/end` で進行を出す。`info["frame_count"] == 0` なら実行せず、エラー「アニメの無い GLB はリアルタイム表示を使ってください」を出す。発散があれば警告「物理が発散しました (最初の検出: ボーン '<名前>')」。
- パネル: 【1】物理の先頭に `engine`。`load_status()` が False なら NATIVE を選べないように `enabled=False` にし、理由をラベルで出して BULLET の UI を出す。NATIVE のときは上記 3 ボタンと「ベイク済み / 未ベイク」表示。BULLET のときは現在の UI をそのまま出す。

- [ ] **Step 1: テストを書く** — `run_headless.py` に `--engine native|bullet` を足し、native のときは読み込み → `mmd2gltf.native_bake` → NLA の存在と NaN 無しを JSON に出す。アニメの無い GLB (`../zatsu_anime/src_f8/work/out.glb`) で `native_bake` が `CANCELLED` になりエラー文言が上記どおりであること。`MMD_NATIVE_LIB=/nonexistent` で native を指定したら、Bullet 方式に切り替わり、`ok` が true で `engine_fallback: true` を出す。
- [ ] **Step 2:** 実行 → FAIL。
- [ ] **Step 3:** 実装する。
- [ ] **Step 4:** `Blender -b -P tests/run_headless.py -- "$MMD_TEST_GLB_SMALL" 60 --engine native` と `--engine bullet` の両方が `"ok": true`。フォールバックのケースも PASS。
- [ ] **Step 5:** `tools/build_zip.sh` で zip を作り、`unzip -l` に `native_lib/libmmdphys.dylib` が含まれる。
- [ ] **Step 6:** コミット (`feat: 物理エンジンの切り替えと配布`)。

---

### Task 12: Windows のビルドと検証 (GitHub Actions)

公開前は実行できない。ワークフローを用意し、公開後に通す。

**Files:**
- Create: `.github/workflows/native.yml`

- [ ] **Step 1:** `windows-latest` で: mmd2gltf-cs と mmd2gltf-ue5-physics-importer を checkout → CMake (`/fp:precise`) でビルド → Task 3 の基準 (CsReference) と Task 4 の CSV (physbake) を同じランナーで作る → `ctest` (層 1〜4 と capi)。`macos-14` でも同じ。テスト GLB は Actions のシークレット URL から取る (リポジトリに入れない)。成果物として `mmdphys.dll` / `libmmdphys.dylib` を上げる。
- [ ] **Step 2:** ローカルでは `act` 等で回さない。YAML の構文だけ `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/native.yml'))"` で確認する。
- [ ] **Step 3:** コミット (`ci: Windows/macOS のネイティブビルドと検証`)。**公開後に初回実行して結果を確認するまで、このタスクは未完了扱い**にする。

---

### Task 13: 実機確認と README (検証の層 8)

**Files:**
- Modify: `README.md`、`README.en.md` (自作エンジンの説明・切り替え・制約・ビルド方法。「設計上の注意」の Bullet 固有の項目は「Blender Bullet 方式を選んだとき」の見出しの下へ移す)

- [ ] **Step 1:** 起動中の Blender に新しいアドオンを入れ直し (前回と同じ手順: 拡張機能フォルダへコピー → 無効化・有効化)、`IA.glb` を読み込む → 自作エンジンでベイク → 再生してスクリーンショットを撮る。リアルタイム表示も同様。
- [ ] **Step 2:** zip から拡張機能としてインストールし、macOS の Gatekeeper に止められないことを確認する (止められたら仕様書 §9 に記録し、署名の方法を見直す)。
- [ ] **Step 3:** README を更新してコミット (`docs: 自作エンジンの説明`)。
