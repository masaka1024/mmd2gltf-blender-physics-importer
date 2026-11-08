# mmd2gltf-blender-physics-importer

**日本語 | [English](README.en.md)**

<!-- mmd2gltf-ecosystem:start -->
> **mmd2gltf エコシステム** — MMD モデル(PMX/VMD)を一度 `.glb` に変換すれば、**Unity・Unreal Engine 5・Blender** のどれにも、物理(揺れ物)と材質(トゥーン/スフィア)つきでそのまま持ち込めます。glTF で表現できない MMD 固有データは `extras.mmd` に元の値のまま残り、各インポーターがそれを読んでエンジン側で再構築します。
>
> ```
> PMX / VMD
>    │  変換 (mmd2gltf-gui または mmd2gltf-cs)
>    ▼
> .glb + extras.mmd   ← 変換はこの 1 回だけ
>    │
>    ├─▶ Unity   … mmd2gltf-unity-physics-importer
>    ├─▶ UE5     … mmd2gltf-ue5-physics-importer
>    └─▶ Blender … mmd2gltf-blender-physics-importer
> ```
>
> | 目的 | 使うもの |
> |---|---|
> | まず変換する(Windows EXE あり) | [mmd2gltf-gui](https://github.com/masaka1024/mmd2gltf-gui) — Python 版。GUI / CLI |
> | 変換する(C# 版、物理ベイク内蔵) | [mmd2gltf-cs](https://github.com/masaka1024/mmd2gltf-cs) — 出力形式は gui と同一。揺れ物を自作エンジンでベイク |
> | Unity で動かす | [mmd2gltf-unity-physics-importer](https://github.com/masaka1024/mmd2gltf-unity-physics-importer) — Editor 拡張。Bullet 互換エンジン同梱(PhysX 不使用) |
> | Unreal Engine 5 で動かす | [mmd2gltf-ue5-physics-importer](https://github.com/masaka1024/mmd2gltf-ue5-physics-importer) — C++ プラグイン。同じエンジンの C++ 移植(Chaos 不使用) |
> | Blender で動かす | `mmd2gltf-blender-physics-importer`(準備中) — アドオン。Blender 内蔵の Bullet に配線 |
> | (ライブラリ)物理エンジン本体 | [mmd2gltf-cs-physics](https://github.com/masaka1024/mmd2gltf-cs-physics) — cs / Unity / UE5 が使う Bullet 2.75 互換エンジン。直接は使いません |
> | (別ルート)Blender の mmd_tools から glTF/FBX を出す | [mmd-to-gltf-exporter](https://github.com/masaka1024/mmd-to-gltf-exporter) — `extras.mmd` は付きません(物理の再構築は対象外) |
>
> 揺れ物の挙動は、Unity と UE5 が同じエンジンを使うため一致します。Blender 版は Blender 内蔵の Bullet で動かすため、動きは近いものの完全には一致しません。
>
> **このリポジトリ**: `.glb` を **Blender** に持ち込む側です。
<!-- mmd2gltf-ecosystem:end -->

`mmd2gltf-gui` が出力した glTF（`.glb`）を Blender に取り込み、モデルの `extras` 領域に保存されている
MMD 固有データ（物理演算設定・トゥーン/スフィアマテリアル設定・IK 定義）を読み取って、
Blender 上で実際に動く形へ自動変換するアドオンです。
[mmd2gltf-unity-physics-importer](https://github.com/masaka1024/mmd2gltf-unity-physics-importer) の Blender 版です。

> **注意**：このアドオンは `mmd2gltf-gui` が出力する特定の GLB 形式（`extras.mmd`）に依存しています。
> 一般的な glTF エクスポーターの出力では物理・材質の再構築は行われません（通常の glTF として読み込まれるだけです）。

> **本プロジェクトは非公式・独立の個人プロジェクトです。**
> MikuMikuDance (樋口優氏)、PmxEditor (極北P氏)、Bullet Physics、およびモデル・モーションの
> 各作者とは、いかなる提携・承認・支援関係もありません。挙動の一致は目標であって保証ではありません。

---

## できること

### 【1】物理演算

揺れ物の計算エンジンを、パネルの **物理エンジン** で切り替えられます（シーンごとの設定）。

| | 自作エンジン（既定） | Blender Bullet |
|---|---|---|
| 計算するもの | Unity / UE5 と**同じ自作エンジン**（Bullet 2.75 互換の C++ 実装）。Blender 内蔵の物理は使わない | Blender 内蔵のリジッドボディ（Bullet） |
| 一致の度合い | **ベイク結果が、Unity / UE5 の再生設定で動かした C# エンジンとビット一致**（macOS arm64 で、同じマシン上の C# エンジンと比べて確認。Windows での一致は未検証） | 近いが一致しない |
| 配線 | 不要（剛体・ジョイントのオブジェクトは作らない） | 必要（ボタンで剛体・ジョイントを生成） |
| 使い方 | ベイク / リアルタイム表示 | タイムラインを先頭から再生 / ベイク |
| 必要なもの | ネイティブライブラリ（現在の zip には macOS arm64 版を同梱。Windows x64 版は CI でのビルドを予定していて、まだありません） | なし |

ネイティブライブラリを読み込めない環境（対応外の OS・CPU、ファイル欠落など）では、自作エンジンは選べなくなり、
パネルに理由を出して Blender Bullet 方式を表示します。

#### 自作エンジン

パネルのボタンは次の 3 つです。どれも対象アーマチュアと、その GLB のパス（読み込み時に記録されます。無ければパネルの GLB 欄）が要ります。

| ボタン | 動作 |
|---|---|
| **物理をベイク (自作エンジン)** | GLB のアニメを入力に全フレームの物理を計算し、揺れ物ボーンのキーを NLA トラック **「MMD Physics Bake」** に書き込む。再生・レンダー・書き出しはこのトラックが担う |
| **ベイクを削除** | 「MMD Physics Bake」トラックと、そのアクションを削除する |
| **リアルタイム表示** | フレーム変更のたびに物理を計算して揺れ物ボーンを動かす（もう一度押すと止める） |

- **ベイクの条件**：Unity / UE5 の再生設定（刻み 1/60 × 2、ソルバ反復 10、ジョイント速度反復 40 など）と、
  UE5 ノードの書き戻し設定（mode 2 の位置合わせ、回転クランプ 0.5）を使います。
  手順は physbake（mmd2gltf-cs）と同じです：先頭フレームの姿勢へ整合 → 2 秒の空回し → 毎フレーム計算。
  入力は GLB 内のアニメです。**Blender 上で編集したアクションはベイクに反映されません。**
- **焼き込み済み GLB**：アクティブなアクションが揺れ物ボーンのキーを持つ GLB（`--bake-physics` など）でも、ベイクが効きます。
  Blender はアクティブなアクションを NLA より上で評価するため、ベイクがある間はアドオンがそのアクションの揺れ物ボーンの fcurve を mute します（キーは変更しません）。
  **ベイクを削除**すると mute を元に戻します。
- **fps**：キーは**ベイク時のシーン fps** に従って打たれます。fps を変えたら、ベイクを削除して取り直してください。
- **リアルタイム表示の性質**（Blender が評価したポーズを入力にするため、ビット一致は求めていません）：
  - 時間を戻す・シークすると、物理をリセットして計算し直します。
  - 空回し（ウォームアップ）をしないので、最初の数フレームは揺れ物が落ち着くまで動きます。
  - ベイク済みのアーマチュアでは何もしません（待機）。ベイクを削除すると有効になります。
  - 止めると、揺れ物ボーンを静止姿勢へ戻します。
  - 計算が発散（NaN）したときは、パネルに警告を出します。

#### Blender Bullet

PMX の剛体・ジョイント設定を読み取り、**Blender 内蔵のリジッドボディ（Bullet）** で駆動します。
ボタン 1 つで配線が終わり、タイムラインを再生すると髪・スカート・リボンなどが揺れます。

| PMX | Blender での写し方 |
|---|---|
| 剛体（球 / 箱 / カプセル） | `rb_<名前>` メッシュ + リジッドボディ（SPHERE / BOX / CAPSULE） |
| モード 0（ボーン追従） | Passive + Animated。Child Of 拘束でボーンに追従 |
| モード 1（物理演算） | Active。ボーン側に Copy Transforms を付けて剛体を追跡 |
| モード 2（物理 + ボーン位置合わせ） | Active。ボーン側は Copy Rotation のみ（位置は親ボーンから） |
| ジョイント（6DOF スプリング） | `joint_<名前>` Empty + Generic Spring 拘束。制限・バネを軸入替で写す |
| 衝突グループ / 非衝突フラグ | 衝突しない組だけ `disable_collisions` の空拘束で無効化（`ncc_*`） |
| 重力 | `9.8 × unitScale`（MMD と揺れの周期を揃える。任意） |

生成物は `MMD Physics` コレクション配下（`Rigid Bodies` / `Joints` / `Non-Collision` / `Bone Track`）にまとまり、
「物理を削除」で一括削除できます。この方式特有の事情は、後述の「Blender Bullet 方式を選んだとき」を参照してください。

### 【2】マテリアル

glTF の Principled BSDF を、MMD 風のノード構成へ置き換えます（EEVEE 向け）。

- **トゥーン**：個別トゥーン（`toonTexture`）と共有トゥーン（`toonShared`、toon01〜10）に対応。
  共有トゥーンの画像は MMD 本体の配布物なので同梱していません。プリファレンスでフォルダを指定すると読み込み、
  無ければ代替の階調画像を生成します。
- **スフィアマップ**：乗算（sph）/ 加算（spa）。カメラ空間法線から UV を作る MatCap 方式。
- **輪郭線**：Solidify モディファイア（法線反転 + 裏面カリング）と材質ごとの edge 材質。
  エッジフラグの無い材質は透明な edge 材質になります。MMD の輪郭線は画面上のピクセル幅ですが
  Solidify は物体空間の太さなので、髪留めのような小物はシェルに飲み込まれて真っ黒になります。
  部品の最小寸法に対してシェルが厚すぎる材質は輪郭線を省きます（「小物の輪郭線を省く比率」）。
- **α復元**：`alphaClass` が `mask` / `blend` の材質は `origTexture`（無加工テクスチャ）の実アルファを
  Dithered（ハッシュ透過）で描きます。Blended は深度を書かないため輪郭線シェルが透けて色が濁ります
  （必要なら手動で Blended に切り替えられます）。
- Blender の glTF インポーターが取り込まないトゥーン / スフィア画像は GLB から直接取り出します。

### 【3】IK（任意）

`nodes[i].extras.mmd.ik` から Blender の IK 拘束を組み直します。
角度制限はボーンローカル軸への近似で写します。膝のように制限のあるリンクには
小さな初期屈曲（既定 10°）をポーズに入れて、伸び切った脚でソルバが逆側（過伸展）へ
曲げようとして止まる問題を回避します（MMD が膝を特別扱いするのと同じ目的）。

---

## 必要なもの

- **Blender 4.2 以降**（開発・検証は 5.2 LTS）。glTF インポーター（標準同梱）が有効であること
- `mmd2gltf-gui`（または `mmd2gltf-cs`）で変換した `.glb`
- 自作エンジンを使う場合：macOS（Apple Silicon）。ライブラリは zip に同梱されます。Windows（x64）は CI でビルドする予定で、まだ同梱していません
- 共有トゥーンを復元したい場合は、MMD 本体同梱の `toon01.bmp`〜`toon10.bmp`

---

## インストール

1. `tools/build_zip.sh` で zip を作る（または Releases の zip を使う）
2. Blender の **編集 > プリファレンス > 拡張機能** 右上の ▼ から **ディスクからインストール** で zip を選ぶ
3. 必要なら同じ画面の「mmd2gltf Physics Importer」設定で共有トゥーンのフォルダを指定する

開発中は `mmd2gltf_blender_importer/` フォルダを Blender の拡張機能フォルダに置いても動きます。

**macOS の注意**：ネイティブライブラリ（`native_lib/libmmdphys.dylib`）は ad-hoc 署名で、公証はしていません。
Blender の「ディスクからインストール」で入れる分には、Blender と同じ方式で展開すると隔離属性が付かないことを確認しています（Python の zipfile で再現。GUI からのインストールは未試行）。ただし、ブラウザで落とした zip を
Finder で展開した**フォルダ**を手で拡張機能フォルダへ置くと、隔離属性が残ってライブラリを読めないことがあります。
その場合はパネルに理由が出るので、ターミナルで `xattr -d com.apple.quarantine <libmmdphys.dylib のパス>` を実行してください。

---

## 使い方

1. **ファイル > インポート > mmd2gltf GLB (.glb) [物理・材質付き]** で `.glb` を選ぶ
   （ダイアログで「物理を配線」「材質を変換」「IK を組む」を選べます）
   （自作エンジンを選んでいる間は「物理を配線」では何も作りません。ライブラリを読めないときは Blender Bullet で配線します）
2. 3D ビューのサイドバー（`N` キー）の **mmd2gltf** タブから、後からでも
   【1】物理を配線 / 再配線 【2】材質を変換 【3】IK を組む を個別に実行できます
3. **物理エンジン** を選ぶ（既定は自作エンジン）。
   - **自作エンジン**：**物理をベイク (自作エンジン)** を押す（配線は不要）。
     ベイクしたら通常どおり再生・レンダー・書き出しができ、途中のフレームへ飛んでも動きます。
     確認だけなら **リアルタイム表示** も使えます（ベイクが無いときだけ働きます）。
   - **Blender Bullet**：次の手順。

   Blender Bullet の場合：**物理キャッシュをリセット** を押してから、**開始フレームから** 再生すると揺れ物が動きます。
   Blender のリジッドボディは順番にしか計算できないので、途中のフレームへ飛ぶと動きません。
   途中から見たい場合や書き出す場合は **物理をベイク** を押してください（6000 フレームで数分）。
4. ビューポートは **マテリアルプレビュー**（または EEVEE レンダー）にしてください。
   剛体・ジョイントは既定で非表示です（パネルの「剛体を表示」「ジョイントを表示」で出せます）

### アニメーション付き GLB

`--vmd` 付きで変換した GLB は、Blender の glTF インポーターがアクションとして読み込みます。
「先頭フレームの姿勢に合わせる」（既定 ON）を有効にしておくと、動的剛体はシーン開始フレームの
ボーン姿勢へ整合させてから置かれます（MMD の物理リセット相当）。

揺れ物ボーンにキーが焼き込まれている GLB（`--bake-physics`）では、Blender Bullet 方式の Copy Transforms が
アニメを上書きするので物理が優先されます。焼き込み結果を使いたい場合は物理を配線しないでください。

---

## 設計上の注意（Unity 版との違い）

### 共通

- **座標系**：`extras.mmd` の raw PMX 値（左手系・未スケール）をワールドで組んでから Blender へ写します。
  位置は `(x, y, z) → (x, z, y) × unitScale`、回転は y/z 入替の鏡映で共役（角度の符号が反転）。
  `physicsGltf`（ボーンローカル値）は、Blender インポーターが作るボーンのローカル軸が
  glTF ノードの軸と一致しないため使いません。
- **IK の初期屈曲**：伸び切った脚では Blender のレガシー IK ソルバが過伸展側へ曲げようとして
  制限に阻まれ、まったく動きません（実測：目標を 25cm 上げても膝 2cm）。膝に 10° の初期屈曲を
  入れると解けるようになります（5° では前上方の目標で失敗）。静止時の膝位置のずれは 2mm 程度です。
  ポールターゲット方式も試しましたが大腿が 180° 捻れる解に落ちるため採用していません。
- **トゥーンの明るさ**：Shader to RGB を使うため **EEVEE 専用**です。Cycles では陰影が出ません。
- **ambient / specular / specularPower**：未対応（Unity 版と同じ）。
- **`sphereMode = 3`（サブテクスチャ）**：未対応。警告を出してスキップします。
- **ボーンモーフ / グループモーフ / 材質モーフ**：未対応（頂点モーフはシェイプキーとして読み込まれます）。

### Blender Bullet 方式を選んだとき

自作エンジンはこれらの影響を受けません（Unity / UE5 と同じエンジンが同じ設定で計算します）。

- **初期めり込みの扱い**：MMD のモデルは髪の根元やスカートの上段が体のコライダーの内側に
  入った状態で作られています。Blender の Bullet はこれを押し出すので、再生と同時にスカートが
  外へ弾かれます（実測：3 フレームで 8cm）。このアドオンは静止姿勢で重なっている組を検出して
  衝突を無効化します（「初期めり込みペアの衝突を無効化」、既定 ON）。Unity 版のエンジンは
  離れた時点で衝突を復帰させますが、Blender の拘束は再生中に切り替えられないため恒久的に無効化します。
- **衝突グループ**：Blender の `collision_collections` は「同じ層にいれば衝突」しか表現できないため、
  PMX のペア指定（双方向マスク）は非衝突拘束で再現しています。組数を抑えるため、既定では
  半径和の 1.5 倍以内の組だけ作ります（「非衝突ペアの距離倍率」。0 で全ペア）。
- **アニメ付きモデルの初回構築**：Blender はリジッドボディワールドを構築するとき、ボーン追従剛体の
  位置を Child Of 拘束の評価前の行列で読みます。バインド位置のままだと、体のコライダーが原点付近に
  ある前提でジョイントが組まれ、再生と同時に髪が 1m 近く引っ張られます（実測）。追従剛体の行列は
  開始フレームの姿勢にし、Child Of の逆行列もその姿勢基準で入れています。
- **焼き込みアニメ付きの揺れ物**：`--bake-physics` で髪ボーンにキーがある GLB では、動的剛体は
  ボーン自身のキーではなく「最初の非物理祖先ボーン」の変位で配置します（Unity 版の FK-rest リセットと
  同じ）。鎖内の剛体とジョイントが同じ変位で動くのでフレームが崩れません。
- **寸法 0 の剛体**：ジョイントの無いダミー剛体（例：`右ドリル2`）を動的にすると自由落下して
  ボーンを引きずるため、ボーン追従に変えて警告を出します。

## 既知の制限

- **自作エンジン：物理 → 非物理 → 物理と続くボーン鎖**は厳密には一致しません（間の非物理ボーンは FK で扱うため）。
- **Blender 4.2〜4.3**（レガシーなアクション API の経路）での自作エンジンのベイクは未検証です（動作確認は 5.1 / 5.2）。
- **Windows 版ライブラリ**は CI でビルドする前提で、リポジトリを公開するまで未ビルドです（現状の zip は macOS arm64 のみ）。
- **Blender 5.1 標準の glTF インポーター**は、`_SDEF_*`・`_EDGESCALE`・`_WEIGHTTYPE` などのカスタム属性を持つ一部の MMD GLB でクラッシュすることがあります。インポーター側の不具合で、このアドオンの問題ではありません。

---

## ネイティブライブラリのビルド

自作エンジンのライブラリは `native/`（CMake）から作ります。配布 zip にはビルド済みが入るので、使うだけなら不要です。

```bash
cmake -S native -B native/build -DCMAKE_BUILD_TYPE=Release
cmake --build native/build
ctest --test-dir native/build
cmake --install native/build     # mmd2gltf_blender_importer/native_lib/ へ置く (macOS は ad-hoc 署名も行う)
```

- **Release でビルドする**こと。`-ffast-math` / `-Ofast` / `/fp:fast` は使いません（浮動小数点の結果が C# エンジンとずれるため。CMake が `-ffp-contract=off` / `/fp:precise` を付けます）。
- `ctest` には、C# 基準とのビット一致テスト（層 2〜4）が含まれます。隣のディレクトリに
  `mmd2gltf-ue5-physics-importer`（`native/vendor/MmdPhysicsCore` の同期検査）と `mmd2gltf-cs`（physbake で基準 CSV を作る。.NET SDK が要る）を
  置いてください。テスト用 GLB は環境変数 `MMD_TEST_GLB_SMALL` / `MMD_TEST_GLB`（大）/ `MMD_TEST_GLB_NOEXTRAS` / `MMD_TEST_GLB_NOANIM` で渡します（リポジトリには含めません）。
  UE5 リポジトリが別の場所にあるときは `-DMMD_UE5_CORE_DIR=...`。
- Windows は Visual Studio（MSVC）でビルドし、`bash`（Git for Windows）が要ります。
- 手元のライブラリを使うだけなら、環境変数 `MMD_NATIVE_LIB` にファイルのフルパスを指定しても読み込めます。

---

## 検証

`tests/run_headless.py` でバックグラウンド実行の検証ができます。

```bash
/Applications/Blender.app/Contents/MacOS/Blender -b -P tests/run_headless.py -- model.glb 60
```

ボーン位置の一致（PMX 由来の計算値との差 < 1mm）、60 フレーム再生後の動的剛体の最大移動量、
NaN の有無を検査して JSON で出力します。

---

## 構成

```
mmd2gltf_blender_importer/
  blender_manifest.toml   拡張機能マニフェスト
  __init__.py             オペレータ・パネル・設定
  glb_reader.py           GLB / extras.mmd の読み取り（bpy 非依存）
  mmd_math.py             PMX raw → Blender の座標変換
  engine_bridge.py        自作エンジン (ネイティブライブラリ) を ctypes で呼ぶ窓口
  selfphys_bake.py        自作エンジンのベイク結果 → NLA トラック「MMD Physics Bake」
  selfphys_live.py        自作エンジンのリアルタイム表示 (フレーム変更ハンドラ)
  native_lib/             ビルド済みライブラリ (libmmdphys.dylib / mmdphys.dll)
  physics_builder.py      Blender Bullet 方式: 剛体・ジョイント・非衝突拘束の生成
  overlap.py              静止姿勢での重なり検出
  material_builder.py     トゥーン / スフィア / 輪郭線 / α のノード構築
  ik_builder.py           IK 拘束の再構築
native/                   自作物理エンジンの C++ ソース (CMake)
  vendor/MmdPhysicsCore/  UE5 版のエンジンを無改変で取り込み
  shim/                   UE 型の代替ヘッダ (互換層)
  src/                    GLB 読み込み・FK・ベイク・C 窓口
  tests/                  C# 基準とのビット一致テストほか (CTest)
tests/run_headless.py     ヘッドレス検証
tools/build_zip.sh        配布 zip の作成
```

`extras.mmd` の仕様の正本は
[mmd2gltf-gui の extrasMmd_schema.md](https://github.com/masaka1024/mmd2gltf-gui/blob/v1.2.0/mmd2gltf/extrasMmd_schema.md)
です。このアドオンが実装している版は **v1.2.0** です。

## ライセンス

MIT License / Copyright (c) 2026 masaka1024 — 全文は [LICENSE](LICENSE) を参照してください。
