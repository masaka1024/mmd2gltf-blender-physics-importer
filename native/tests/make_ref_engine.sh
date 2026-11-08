#!/bin/bash
# C# 参照エンジンで基準 CSV を作る (層 2)。
# usage: make_ref_engine.sh <out.csv> [glb] [frames]
# 環境変数: MMD_UE5_REPO (既定 ../../mmd2gltf-ue5-physics-importer)、MMD_TEST_GLB_SMALL
set -euo pipefail
OUT="${1:?usage: make_ref_engine.sh <out.csv> [glb] [frames]}"
GLB="${2:-${MMD_TEST_GLB_SMALL:-}}"
if [ -z "$GLB" ]; then
  echo "error: テスト GLB がありません。引数 [glb] か環境変数 MMD_TEST_GLB_SMALL で渡してください (native/tests/local_env.example.sh)" >&2
  exit 1
fi
FRAMES="${3:-300}"
HERE="$(cd "$(dirname "$0")" && pwd)"
UE5="${MMD_UE5_REPO:-$HERE/../../../mmd2gltf-ue5-physics-importer}"
UE5="$(cd "$UE5" && pwd)"
# Git Bash (msys) の /d/a/... を dotnet が読める C:/... 形式へ (macOS/Linux では何もしない)
if command -v cygpath >/dev/null 2>&1; then UE5="$(cygpath -m "$UE5")"; fi
PIN=74fcc16

if [ "$(git -C "$UE5/.upstream/unity" rev-parse --short=7 HEAD 2>/dev/null || true)" != "$PIN" ]; then
  echo "error: $UE5/.upstream/unity が $PIN ではありません。次で用意してください:" >&2
  echo "  git clone https://github.com/masaka1024/mmd2gltf-unity-physics-importer $UE5/.upstream/unity && git -C $UE5/.upstream/unity checkout $PIN" >&2
  exit 1
fi

# csproj は net9.0 だが dotnet 10 しか無い環境でも動かす。成果物は UE5 リポを汚さない場所へ。
WORK="$(dirname "$OUT")/csref_build"
mkdir -p "$WORK"
export DOTNET_ROLL_FORWARD=Major DOTNET_CLI_TELEMETRY_OPTOUT=1
dotnet build "$UE5/Tools/CsReference/MmdCsReference.csproj" -c Release -v q --nologo \
  -p:BaseIntermediateOutputPath="$WORK/obj/" -p:OutDir="$WORK/bin/" >&2
dotnet "$WORK/bin/MmdCsReference.dll" "$GLB" "$FRAMES" "$OUT" --per-frame --drive --playback
