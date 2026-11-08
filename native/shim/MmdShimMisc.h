// FFileHelper / FPlatformTime。
#pragma once

#include "MmdShimContainers.h"
#include "MmdShimString.h"

struct FFileHelper
{
	static bool LoadFileToArray(TArray<uint8>& Result, const TCHAR* Filename);
};

struct FPlatformTime
{
	static double Seconds();
};
