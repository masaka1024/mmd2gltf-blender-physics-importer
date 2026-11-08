#!/usr/bin/env bash
# 拡張機能 (Blender 4.2+ Extension) の zip を dist/ に作る。
# 使い方: tools/build_zip.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(grep -E '^version' mmd2gltf_blender_importer/blender_manifest.toml | sed -E 's/.*"([^"]+)".*/\1/')
mkdir -p dist
OUT="dist/mmd2gltf_blender_importer-${VER}.zip"
rm -f "$OUT"
# native_lib/ (自作物理エンジン) は macOS の dylib / Windows の dll のうち、存在するものを含める。
# マニフェストの platforms はライブラリの無いプラットフォームも含む (読めなければ Bullet 方式に切り替わる)。
for f in libmmdphys.dylib mmdphys.dll; do
  [ -f "mmd2gltf_blender_importer/native_lib/$f" ] || echo "注意: native_lib/$f がありません (この zip には含まれません。そのプラットフォームでは Blender Bullet 方式で動きます)"
done
# __pycache__ を除外して、パッケージディレクトリをそのまま zip 化する
zip -r -q "$OUT" mmd2gltf_blender_importer -x '*/__pycache__/*' '*.pyc'
echo "wrote $OUT"
echo "Blender で: 編集 > プリファレンス > 拡張機能 > (右上の▼) > ディスクからインストール で選ぶ"
