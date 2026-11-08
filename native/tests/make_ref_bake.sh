#!/bin/bash
# C# physbake で基準 CSV (FK と物理ベイク) を作る (層 3 / 層 4 共通)。
# usage: make_ref_bake.sh <glb> <out_prefix>   -> <out_prefix>.fk.csv, <out_prefix>.bake.csv
# 環境変数: MMD_CS_REPO (既定 /Users/masaka1024/KAIHATU/mmd2gltf-cs)
# 成果物は <out_prefix> のあるディレクトリ (CMake の build 配下) の csref_physbake/ に出す。リポジトリは汚さない。
set -euo pipefail
GLB="${1:?usage: make_ref_bake.sh <glb> <out_prefix>}"
PREFIX="${2:?usage: make_ref_bake.sh <glb> <out_prefix>}"
CS="${MMD_CS_REPO:-/Users/masaka1024/KAIHATU/mmd2gltf-cs}"
BRANCH=feat/physbake-playback

cur="$(git -C "$CS" rev-parse --abbrev-ref HEAD)"
if [ "$cur" != "$BRANCH" ]; then
  echo "error: $CS は $BRANCH をチェックアウトしてください (現在: $cur)" >&2
  exit 1
fi

OUTDIR="$(dirname "$PREFIX")"
mkdir -p "$OUTDIR"
WORK="$(cd "$OUTDIR" && pwd)/csref_physbake"
mkdir -p "$WORK"
if command -v cygpath >/dev/null 2>&1; then WORK="$(cygpath -m "$WORK")"; fi
export DOTNET_ROLL_FORWARD=Major DOTNET_CLI_TELEMETRY_OPTOUT=1

# 同じディレクトリに並列で呼ばれてもビルドが衝突しないようにロックする。
LOCK="$WORK/.lock"
while ! mkdir "$LOCK" 2>/dev/null; do sleep 1; done
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT
dotnet build "$CS/src/Mmd2Gltf.PhysBake/Mmd2Gltf.PhysBake.csproj" -c Release -v q --nologo \
  -o "$WORK/bin" >&2
rmdir "$LOCK" 2>/dev/null || true
trap - EXIT

dotnet "$WORK/bin/physbake.dll" "$GLB" --playback --dump-fk "$PREFIX.fk.csv" --dump-bake "$PREFIX.bake.csv" --dry-run -q
