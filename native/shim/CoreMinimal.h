// UE5 CoreMinimal.h の互換層 (native/shim)。MmdPhysicsCore が使う最小限の UE 型だけを再現する。
// 算術は一切変えない: FMath は標準ライブラリの同名関数をそのまま呼ぶ (UE が素の std と違う所だけ UE に合わせる)。
#pragma once

#include "MmdShimCore.h"
#include "MmdShimContainers.h"
#include "MmdShimString.h"
#include "MmdShimMisc.h"
