// C# の float.ToString("R") で書いた CSV 値を float32 に戻し、ビット単位で比べるための共通処理。
// Task 5・6 のテストからも使う。
#pragma once
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>

// "R" 形式 (最短の往復可能表現。"1E-05"、"NaN"、"Infinity"、"-Infinity" もあり得る) を
// 正しく丸めて float にする。全体が数値として読めなければ false。
inline bool ParseRoundTripFloat(const std::string& S, float& Out)
{
	if (S.empty()) return false;
	if (S == "NaN") { Out = std::numeric_limits<float>::quiet_NaN(); return true; }
	if (S == "Infinity") { Out = std::numeric_limits<float>::infinity(); return true; }
	if (S == "-Infinity") { Out = -std::numeric_limits<float>::infinity(); return true; }
	// strtof は "inf" "nan" "0x.." 等も受けるので、10 進の文字だけに限定する。
	for (char C : S)
	{
		if (!((C >= '0' && C <= '9') || C == '-' || C == '+' || C == '.' || C == 'E' || C == 'e')) return false;
	}
	const char* Begin = S.c_str();
	char* End = nullptr;
	errno = 0;
	const float V = std::strtof(Begin, &End);   // 直接 float へ丸める (double 経由の二重丸めを避ける)
	if (End == Begin || *End != '\0') return false;
	Out = V;
	return true;   // 範囲外は ±inf / 0 になるが、"R" の出力ではあり得ない
}

inline uint32_t FloatBits(float V)
{
	uint32_t B;
	std::memcpy(&B, &V, sizeof(B));
	return B;
}

// IEEE754 ビット列の 8 桁大文字 16 進。
inline std::string Hex8(float V)
{
	char Buf[16];
	std::snprintf(Buf, sizeof(Buf), "%08X", FloatBits(V));
	return Buf;
}
