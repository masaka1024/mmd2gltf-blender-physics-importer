// UE のログマクロ互換 (何もしない)。
#pragma once
#include "CoreMinimal.h"

#define DECLARE_LOG_CATEGORY_EXTERN(CategoryName, DefaultVerbosity, CompileTimeVerbosity) \
	extern int MmdShimLogCategory_##CategoryName
#define DEFINE_LOG_CATEGORY(CategoryName) int MmdShimLogCategory_##CategoryName = 0
#define DEFINE_LOG_CATEGORY_STATIC(CategoryName, DefaultVerbosity, CompileTimeVerbosity) \
	static int MmdShimLogCategory_##CategoryName = 0
#define UE_LOG(CategoryName, Verbosity, Format, ...) ((void)0)
