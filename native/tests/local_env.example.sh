#!/bin/bash
# ネイティブテスト / ヘッドレステスト用の環境変数の例。
# コピーして自分のパスに書き換え、configure と ctest の前に読み込む:
#   cp native/tests/local_env.example.sh native/tests/local_env.sh   # local_env.sh は .gitignore 済み
#   source native/tests/local_env.sh
#   cmake -S native -B native/build && cmake --build native/build && ctest --test-dir native/build
# テスト GLB はリポジトリに入れない。
#
# 小さい GLB (アニメ付き。層 2〜4 / C API / ヘッドレス)
export MMD_TEST_GLB_SMALL="/path/to/small_model.glb"
# 大きい GLB (アニメ付き。層 3〜4 の big。焼き込み済み物理キー入りなら headless_selfphys_override.py にも使う)
export MMD_TEST_GLB="/path/to/big_model.glb"
export MMD_TEST_GLB_PREBAKED="$MMD_TEST_GLB"
# extras.mmd を持たない GLB / アニメを持たない GLB (C API テスト)
export MMD_TEST_GLB_NOEXTRAS="/path/to/no_extras.glb"
export MMD_TEST_GLB_NOANIM="/path/to/no_animation.glb"
# 参照実装のリポジトリ (既定はこのリポジトリと同じ階層。別の場所なら指定)
# export MMD_UE5_REPO="/path/to/mmd2gltf-ue5-physics-importer"
# export MMD_CS_REPO="/path/to/mmd2gltf-cs"
