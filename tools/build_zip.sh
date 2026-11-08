#!/usr/bin/env bash
# 拡張機能 (Blender 4.2+ Extension) の zip を dist/ に作る。
# 使い方: tools/build_zip.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(grep -E '^version' mmd2gltf_blender_importer/blender_manifest.toml | sed -E 's/.*"([^"]+)".*/\1/')
mkdir -p dist
OUT="dist/mmd2gltf_blender_importer-${VER}.zip"
rm -f "$OUT"
# __pycache__ を除外して、パッケージディレクトリをそのまま zip 化する
zip -r -q "$OUT" mmd2gltf_blender_importer -x '*/__pycache__/*' '*.pyc'
echo "wrote $OUT"
echo "Blender で: 編集 > プリファレンス > 拡張機能 > (右上の▼) > ディスクからインストール で選ぶ"
