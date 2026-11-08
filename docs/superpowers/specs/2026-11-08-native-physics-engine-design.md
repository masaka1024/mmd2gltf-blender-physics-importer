# 自作物理エンジン (C++) への切り替え — 設計

- 日付: 2026-11-08
- 対象: `mmd2gltf-blender-physics-importer`
- 状態: 設計承認済み / 実装計画は未作成

## 1. 目的

Blender 版の揺れ物を、Unity / UE5 と**同じ自作エンジン**で計算できるようにする。
現在の Blender 内蔵 Bullet 方式は「近いが一致しない」ため、エコシステムの中で Blender だけ揺れ方が違う。

## 2. 要件 (ユーザー決定事項)

| 項目 | 決定 |
|---|---|
| 用途 | **ベイク** (レンダー・書き出し) と **リアルタイム表示** の両方 |
| 既存の Blender Bullet 方式 | パネルで切り替えられるように**残す**。ライブラリが無い環境の受け皿にもなる |
| 対応 OS | **macOS arm64** と **Windows x64** |
| 合格基準 | ベイク結果が **Unity / UE5 の再生設定**で動かした C# エンジンと**ビット一致** |

### 2.1 ビット一致の範囲 (前提)

- 対象は **GLB 内のアニメを入力にしたベイク**だけ。比較点は **C++ が返す値** (MMD 座標の float32)。
  Blender 用への座標変換と NLA への書き込みは比較対象外 (§7 の層 5 で別に検査する)。
- リアルタイム表示は Blender が評価したポーズを入力にするため、**近いことが目標**で、ビット一致は求めない。
- 浮動小数点の結果は OS / CPU で変わりうるので、比較は**同じマシン上の C# 基準**と行う。
- 「Unity / UE5 の再生設定」とは、`FAnimNode_MmdPhysics` の既定値を指し、2 種類ある:
  - **ソルバ設定** (= `Tools/CsReference` の `ApplyPlaybackSettings`): 刻み 1/60 × 2、`SolverIterations` 10、
    `JointVelocityIterations` 40、`JointMaxCorrectionVel` 30、`UseSplitImpulse` / `UseJointSplitImpulse` ON。
    重力 98 はコア既定と同じ。
  - **書き戻し設定** (ノードが `ComputeAlignedBonePoses` に渡す値): mode2 位置合わせ ON、
    `bAlignBonePositions` OFF、`AlignRotClampAlpha` **0.5**。physbake の現状は 0 (既定引数) なので、
    `--playback` ではこれも 0.5 にする。
  値は実装時にノードと `ApplyPlaybackSettings` から写し、C# 基準と C++ で**同じ表を 1 か所ずつ**持つ (§7 層 4 で一致を検査)。
- ベイクの手順は physbake の `PhysicsBaker.Run` と同じ: FK-rest でフレーム 0 姿勢へ整合 → 2 秒の空回し →
  毎フレーム `ApplyKinematicTargets` → `StepSimulation(dt)` → 揺れ物ボーン姿勢の算出。

### 2.2 対象外

- Blender で編集したアクションをビット一致でベイクすること (ベイク入力は GLB のアニメ)。
- ベイクのキャンセル。
- Linux 版のライブラリ (CMake は OS 非依存に書くので後から足せる)。

## 3. 方式

**C 言語の窓口を持つ共有ライブラリを Python の `ctypes` で呼ぶ。**

- UE5 版 `MmdPhysicsCore` のソースを**無改変で取り込み**、UE 型の代替ヘッダ (互換層) でビルドする。
  Unity / UE5 / Blender が同じエンジンのソースを共有し、「1:1 直訳」の方針を保てる。
- `ctypes` なので Blender の Python のバージョンに依存しない (4.2〜5.x で同じバイナリ)。

不採用案: pybind11 等の拡張モジュール (OS × Python 版数のビルドが要る)、physbake を外部プロセスで呼ぶ
(.NET 同梱が要り、リアルタイム表示ができない)。

## 4. 構成

```
mmd2gltf-blender-physics-importer/
  native/
    CMakeLists.txt
    vendor/MmdPhysicsCore/     UE5 版 Public/ Private/ を無改変コピー (Tests/ と *Module.cpp は除く)
    shim/                      CoreMinimal.h, Containers, Math, Templates, HAL, Misc … UE 型の代替
    src/mmd_fk.cpp/.h          physbake の FK を 1:1 移植 (GltfAnim.SampleLocal / MmdConvert.GltfToMmd / 親子の積み上げ)
    src/mmd_bake.cpp/.h        PhysicsBaker.Run を 1:1 移植 + 再生設定の適用
    src/mmd_capi.cpp/.h        extern "C" の窓口
    tests/                     CTest で回すテスト用プログラム (外部ライブラリ不使用)
    tools/sync_check.py        UE5 版とのハッシュ比較
  mmd2gltf_blender_importer/
    native_lib/                ビルド済み libmmdphys.dylib / mmdphys.dll
    engine_bridge.py           ctypes の包み (読み込み・型・エラー)
    selfphys_bake.py           ベイク結果 → アクション / NLA
    selfphys_live.py           リアルタイム (フレーム変更ハンドラ)
    physics_builder.py ほか    既存の Blender Bullet 方式 (変更しない)
```

### 4.1 各部分

- **vendor/MmdPhysicsCore**: 書き換え禁止。`sync_check.py` が UE5 リポジトリの同名ファイルと
  「コメント・空白・BOM を除いたハッシュ」で比較し、ずれていれば CMake の構成段階で失敗させる
  (UE5 リポジトリが見つからない環境では警告のみ。C# 側 SyncGuard と同じ扱い)。
- **shim**: vendor が使う UE 型だけを再現する。対象は計数済み — `TArray` / `FString` / `TMap` / `FMath` /
  `TOptional` / `TSharedPtr` / `TFunctionRef` / `UE_LOG` / `FFileHelper` / `int32`・`int64` など。
  演算順序を変える実装 (例: `FMath` 関数の別アルゴリズム化) をしてはならない。UE の実装と同じ式にする。
- **mmd_fk / mmd_bake**: C# からの 1:1 移植。改善や整理はしない。移植元のファイルと行範囲をコメントに残す。
- **C 窓口** (案。名前と引数は実装計画で確定する):
  - `mmd_api_version()`
  - `mmd_create(utf8_glb_path) -> handle` / `mmd_destroy(handle)`
  - `mmd_info(handle, …)` — ボーン数・揺れ物ボーンの index 列・フレーム数・unitScale
  - `mmd_bake(handle, anim_index, out_buffer, out_capacity, progress_cb)` — [フレーム × 揺れ物ボーン] の位置 + 回転 (float32)
  - `mmd_live_reset(handle, bone_world_in)` / `mmd_live_step(handle, bone_world_in, dt, bone_world_out)`
  - `mmd_last_error() -> utf8`
- **パネル**: 【1】物理の先頭に「エンジン: 自作 / Blender Bullet」の切り替えを置く。
  自作のときは「物理をベイク (自作エンジン)」「ベイクを削除」「リアルタイム表示」を出す。

### 4.2 ビルド

- CMake。浮動小数点は UE5 版 (`FPSemantics = Precise`) に合わせる: clang `-ffp-contract=off`、MSVC `/fp:precise`。
  `-ffast-math` 系は使わない。
- macOS arm64 はこの Mac でビルドする。Windows x64 は GitHub Actions でビルドする (リポジトリ公開後)。
- 拡張機能マニフェストに `platforms = ["macos-arm64", "windows-x64"]` を書き、両方のライブラリを同梱した zip を作る。
- macOS 版は ad-hoc codesign する。

## 5. データの流れ

### 5.1 ベイク

```
GLB ─▶ [C++] GLB 読み込み → FK (補間 → MMD 座標 → 親子の積み上げ)
              → FK-rest 整合 → 2 秒の空回し → 毎フレーム 駆動 → 計算 → 揺れ物ボーン姿勢
     ◀── [フレーム × 揺れ物ボーン] のワールド姿勢 (float32, MMD 座標)   ← ビット一致の比較点
[Python] MMD 座標 → Blender のアーマチュア空間 → ボーンのローカル姿勢 (既存 mmd_math.py)
       → アクション「MMD Physics Bake」に揺れ物ボーンだけキーを打ち、NLA の上段 (Replace) に置く
```

- 元のアクションは変更しない。焼き直すと NLA の段を差し替え、「ベイクを削除」で段とアクションを消す。
- 進行表示は `window_manager.progress_*` を使う。

### 5.2 リアルタイム

```
frame_change_post ハンドラ:
  開始フレーム、または前回 +1 以外のフレーム → mmd_live_reset (評価済みポーズへ剛体を整合)
  前回 +1 のフレーム → 評価済みポーズを MMD 座標へ変換 → mmd_live_step(dt = 1/fps) → pose_bone に書き込む
```

- 入力は Blender の評価済みポーズ (手付けやアクション編集も反映される)。
- 逆再生・シークはリセット (UE5 版のワープ検出と同じ扱い)。
- 「MMD Physics Bake」の段がある間はリアルタイム計算を止める。
- **先に確かめる危険箇所**: ハンドラ内で pose_bone を書き換えたとき、表示とレンダーに同じフレームで反映されるか。
  実装計画の最初の作業として小さな試作で確認し、駄目なら書き込み方法を見直す。

## 6. エラー処理

| 状況 | 動作 |
|---|---|
| ライブラリが無い / CPU 種別違い / `mmd_api_version` 不一致 | 自作エンジンを選べなくし、パネルに理由を表示。Blender Bullet 方式は使える |
| C++ 内の例外 | 窓口の全関数で捕まえてエラーコードを返す。詳細は `mmd_last_error()` |
| GLB に `extras.mmd` が無い | エラーで中止 |
| ベイクでアニメが無い | エラー「アニメの無い GLB はリアルタイム表示を使ってください」 |
| 発散 (NaN) | 直前の姿勢を保持し、最初に発散したボーン名を警告 (physbake と同じ) |
| PMX ボーンに対応する Blender ボーンが無い | そのボーンを飛ばし、件数を警告 |
| リアルタイム中の例外 | 警告を 1 回出してリアルタイム計算を止める |

- 結果のバッファは **Python 側が確保**して渡す (所有権を C++ に持たせない)。
- GLB パスは UTF-8 で渡し、Windows では C++ 側でワイド文字へ変換して開く。
- ハンドルはファイル読み込み (`load_pre`) とアドオン無効化で `mmd_destroy` する。

## 7. 検証

テスト素材: `~/Downloads/IA 2/IA.glb` (68MB、アニメのチャンネル 257) を主に、
`~/Desktop/IA_Conqueror_v4のコピー.glb` (18MB) を軽量確認に使う。どちらもリポジトリには入れない (パスを引数で渡す)。

| 層 | 対象 | 比べる相手 | 合格条件 |
|---|---|---|---|
| 1 | vendor の同一性 | UE5 リポジトリの `MmdPhysicsCore` | 正規化ハッシュ一致 |
| 2 | エンジン単体 (互換層込み) | `Tools/CsReference --per-frame --drive --playback` の CSV | 全剛体・全フレームで差 0 |
| 3 | FK | physbake に足す FK ダンプの CSV | 全ボーン・全フレームで差 0 |
| 4 | ベイク全体 | physbake に足す `--playback` + ボーン姿勢 CSV 出力 | 揺れ物ボーン・全フレームで差 0 (macOS / Windows それぞれ同じマシンの C# と) |
| 5 | Blender への書き込み | 層 4 の出力を Python で変換した値 | ボーン位置の差 < 1e-4、NLA 段の作成・差し替え・削除 |
| 6 | 切り替え・失敗時 | ― | ライブラリを外すと Bullet 方式で動く。版数違いは読み込み拒否 |
| 7 | リアルタイム | ― | 60 フレーム再生で例外・NaN なし。ベイクとの差は記録のみ |
| 8 | 実機 | ― | 起動中の Blender で読み込み・ベイク・再生し、スクリーンショットで確認 |

- 層 1〜4 は CTest、層 5〜7 は `tests/run_headless.py` の拡張で回す。
- Windows の層 2〜4 は GitHub Actions で C# と C++ を同じランナーで動かす。公開までは macOS のみで検証する。

## 8. 他リポジトリへの変更

- **mmd2gltf-cs** (`Mmd2Gltf.PhysBake`): `--playback` (再生設定の適用)、`--dump-fk <csv>`、`--dump-bake <csv>` を追加する。
  出力 GLB の内容は既存オプションでは変えない。
- **mmd2gltf-ue5-physics-importer**: 変更しない (取り込み元として読むだけ)。

## 9. 未解決・リスク

- **解決済み: ハンドラでのポーズ書き込み方式 (§5.2)。** 試作 (Blender 5.2.1 `-b`、IA_Conqueror_v4 の「前髪R」に毎フレーム回転を書く、フレーム 1〜10 / レンダーは 1〜5) で次の結果を得た。
  - **採用: `frame_change_post` で `pose_bone` に書き、直後に `bpy.context.view_layer.update()` を呼ぶ。** 遅れ 0 フレーム。
    アニメーション・レンダー (EEVEE / Workbench とも) の各フレームの画素が、同じポーズを手で当てた参照レンダーと全フレーム一致した。
    `frame_set` 後の静止画レンダーも一致した。ハンドラ内で読む評価済みポーズ (入力) は、そのフレームの値だった。
  - 不採用 (A): `frame_change_post` で書くだけ (`update()` なし)。`frame_set` 後の評価済みポーズは遅れ 0 (次の評価で追いつくため) だが、
    **レンダーのアニメーションではフレーム 2〜5 が参照のどのフレームとも一致しなかった** (フレーム 1 のみ一致)。レンダー用の評価が書き込み前のため。使えない。
  - 不採用 (B): `frame_change_pre` で書く。出力は 0 フレーム遅れで一致するが、ハンドラの時点ではそのフレームのアニメーションが未評価で、
    入力 (評価済みポーズ) が 1 フレーム前になるため、§5.2 の「同フレームの評価済みポーズを入力にする」を満たせない。
  - 不採用 (C): `depsgraph_update_post`。バックグラウンド実行の `frame_set` ではポーズが書き込まれず (全フレームで 0° のまま)、
    アニメーションレンダーでは全フレームが最後の値 (10°) になった。使えない。
  - 実装 (タスク 10) では、ハンドラ内で `view_layer.update()` を呼ぶため、書き込みで更新が再入しないよう再入ガードを置く。
    GUI のビューポート表示そのものは未確認 (バックグラウンドでは画面描画がない)。実機確認はタスク 10 の手動検証に含める。
- 互換層の `FMath` 等が UE と同じ演算をしているか。層 2 のビット一致で検出する。
- Windows のビルドと検証は GitHub 公開が前提。公開はまだ (git 化もまだ)。
- macOS で zip から入れたライブラリが Gatekeeper に止められないか。実機で確認する。
