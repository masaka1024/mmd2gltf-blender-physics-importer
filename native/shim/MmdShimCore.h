// 基本型・マクロ・FMath・FMemory (UE5 互換)。
#pragma once

#include <cmath>
#include <math.h>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <limits>
#include <type_traits>
#include <utility>

#if defined(_WIN32)
	#define PLATFORM_WINDOWS 1
	#define PLATFORM_MAC 0
#elif defined(__APPLE__)
	#define PLATFORM_WINDOWS 0
	#define PLATFORM_MAC 1
#else
	#define PLATFORM_WINDOWS 0
	#define PLATFORM_MAC 0
#endif

#define MMDPHYSICSCORE_API
#define FORCEINLINE inline
#define INDEX_NONE (-1)
// UE5: PI は float リテラル (PI / 2.0 は float を double へ昇格して割る)。UE_PI は double。
#define PI (3.1415926535897932f)
#define UE_PI (3.1415926535897932)

using int8 = std::int8_t;
using int16 = std::int16_t;
using int32 = std::int32_t;
using int64 = std::int64_t;
using uint8 = std::uint8_t;
using uint16 = std::uint16_t;
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using ANSICHAR = char;
using TCHAR = char16_t;
#define TEXT(x) u##x

// UE の checkf は Shipping では消える。ここでは失敗時に落とす (正常系の算術には影響しない)。
#define checkf(expr, ...) do { if (!(expr)) { std::fprintf(stderr, "checkf failed: %s (%s:%d)\n", #expr, __FILE__, __LINE__); std::abort(); } } while (0)
#define check(expr) checkf(expr, "")

template <typename T> inline typename std::remove_reference<T>::type&& MoveTemp(T&& Obj) noexcept { return static_cast<typename std::remove_reference<T>::type&&>(Obj); }
template <typename T> inline void Swap(T& A, T& B) { T Tmp(MoveTemp(A)); A = MoveTemp(B); B = MoveTemp(Tmp); }

// UE Math/NumericLimits.h:28-29,248-266: MIN_flt 1.175494351e-38F, MAX_flt 3.402823466e+38F (= std 値), Lowest = -Max。
template <typename T> struct TNumericLimits : std::numeric_limits<T>
{
	static constexpr T Max() noexcept { return std::numeric_limits<T>::max(); }
	static constexpr T Min() noexcept { return std::numeric_limits<T>::min(); }
	static constexpr T Lowest() noexcept { return std::numeric_limits<T>::lowest(); }
};

// FMath: UE 5.8 の式をそのまま写す (出典は UE_5.8/Engine/Source/Runtime/Core 以下)。
//  GenericPlatformMath.h:944-971  Max(A,B) = (B < A) ? A : B ; Min(A,B) = (A < B) ? A : B  (±0・NaN の符号/結果が違う)
//  UnrealMathUtility.h:592-595    Clamp(X,Lo,Hi) = Max(Min(X, Hi), Lo)
//  GenericPlatformMath.h:930-933 (汎用) Abs(A) = (A < 0) ? -A : A。ただし 1346-1355 で float=fabsf / double=fabs に特殊化されている (Abs(-0.0f) = +0.0f)
//  GenericPlatformMath.h:521-556  Sin/Cos/Sqrt/Atan/Pow = sinf/sin 等。Acos/Asin は引数を [-1,1] に丸めてから呼ぶ
//  GenericPlatformMath.cpp:78-86  Atan2(double) は X==0 && Y==0 なら 0.0 を返す。float 版は多項式近似なので delete
//  UnrealMath.cpp:2969-3031       RoundHalfToEven / TruncateToHalfIfClose
class FMath
{
public:
	template <typename T> static constexpr FORCEINLINE T Max(T A, T B) { return (B < A) ? A : B; }
	template <typename T> static constexpr FORCEINLINE T Min(T A, T B) { return (A < B) ? A : B; }
	template <typename T> static constexpr FORCEINLINE T Clamp(const T X, const T MinValue, const T MaxValue) { return Max(Min(X, MaxValue), MinValue); }
	// Abs<float>/Abs<double> も明示指定で同じ特殊化に入るよう、テンプレート内で分岐する。
	template <typename T> static FORCEINLINE T Abs(const T A)
	{
		if constexpr (std::is_same_v<T, float>) { return fabsf(A); }
		else if constexpr (std::is_same_v<T, double>) { return fabs(A); }
		else { return (A < (T)0) ? (T)-A : A; }
	}

	static FORCEINLINE float Sqrt(float V) { return sqrtf(V); }
	static FORCEINLINE double Sqrt(double V) { return sqrt(V); }
	static FORCEINLINE float Sin(float V) { return sinf(V); }
	static FORCEINLINE double Sin(double V) { return sin(V); }
	static FORCEINLINE float Cos(float V) { return cosf(V); }
	static FORCEINLINE double Cos(double V) { return cos(V); }
	static FORCEINLINE float Atan(float V) { return atanf(V); }
	static FORCEINLINE double Atan(double V) { return atan(V); }
	static FORCEINLINE double Atan2(double Y, double X)
	{
		if (X == 0.0 && Y == 0.0) { return 0.0; }
		return atan2(Y, X);
	}
	static float Atan2(float Y, float X) = delete;
	static FORCEINLINE float Acos(float V) { return acosf((V < -1.f) ? -1.f : ((V < 1.f) ? V : 1.f)); }
	static FORCEINLINE double Acos(double V) { return acos((V < -1.0) ? -1.0 : ((V < 1.0) ? V : 1.0)); }
	static FORCEINLINE float Asin(float V) { return asinf((V < -1.f) ? -1.f : ((V < 1.f) ? V : 1.f)); }
	static FORCEINLINE double Asin(double V) { return asin((V < -1.0) ? -1.0 : ((V < 1.0) ? V : 1.0)); }
	static FORCEINLINE float Pow(float A, float B) { return powf(A, B); }
	static FORCEINLINE double Pow(double A, double B) { return pow(A, B); }

	// UnrealMath.cpp:2969-3031 (UE_SMALL_NUMBER = 1.e-8f は double 版の既定引数にも float のまま渡る)
	static FORCEINLINE float Modf(const float V, float* Int) { return modff(V, Int); }
	static FORCEINLINE double Modf(const double V, double* Int) { return modf(V, Int); }
	static FORCEINLINE float TruncateToHalfIfClose(float F, float Tolerance = 1.e-8f)
	{
		float I = 0.0f;
		float Fr = Modf(F, &I);
		if (F < 0.0f) { return I + ((Abs<float>(Fr - -0.5f) <= Tolerance) ? -0.5f : Fr); }
		else { return I + ((Abs<float>(Fr - 0.5f) <= Tolerance) ? 0.5f : Fr); }
	}
	static FORCEINLINE double TruncateToHalfIfClose(double F, double Tolerance = 1.e-8f)
	{
		double I = 0.0;
		double Fr = Modf(F, &I);
		if (F < 0.0) { return I + ((Abs<double>(Fr - -0.5) <= Tolerance) ? -0.5 : Fr); }
		else { return I + ((Abs<double>(Fr - 0.5) <= Tolerance) ? 0.5 : Fr); }
	}
	static float RoundHalfToEven(float F)
	{
		F = TruncateToHalfIfClose(F);
		const bool bIsNegative = F < 0.0f;
		const bool bValueIsEven = static_cast<uint32>(floorf(((bIsNegative) ? -F : F))) % 2 == 0;
		if (bValueIsEven) { return (bIsNegative) ? floorf(F + 0.5f) : ceilf(F - 0.5f); }
		else { return (bIsNegative) ? ceilf(F - 0.5f) : floorf(F + 0.5f); }
	}
	static double RoundHalfToEven(double F)
	{
		F = TruncateToHalfIfClose(F);
		const bool bIsNegative = F < 0.0;
		const bool bValueIsEven = static_cast<uint64>(floor(((bIsNegative) ? -F : F))) % 2 == 0;
		if (bValueIsEven) { return (bIsNegative) ? floor(F + 0.5) : ceil(F - 0.5); }
		else { return (bIsNegative) ? ceil(F - 0.5) : floor(F + 0.5); }
	}
};

// ハッシュ (TSet/TMap の内部では使わない。Vec3 の GetTypeHash 用。値の算術には関与しない)。
inline uint32 GetTypeHash(const uint32 V) { return V; }
inline uint32 GetTypeHash(const int32 V) { return static_cast<uint32>(V); }
inline uint32 GetTypeHash(const float V) { uint32 B; std::memcpy(&B, &V, sizeof B); return B; }
inline uint32 HashCombine(uint32 A, uint32 C)
{
	uint32 B = 0x9e3779b9;
	A += B;
	B = 0x9e3779b9;
	A -= B; A -= C; A ^= (C >> 13);
	B -= C; B -= A; B ^= (A << 8);
	C -= A; C -= B; C ^= (B >> 13);
	A -= B; A -= C; A ^= (C >> 12);
	B -= C; B -= A; B ^= (A << 16);
	C -= A; C -= B; C ^= (B >> 5);
	A -= B; A -= C; A ^= (C >> 3);
	B -= C; B -= A; B ^= (A << 10);
	C -= A; C -= B; C ^= (B >> 15);
	return C;
}

struct FMemory
{
	static FORCEINLINE void* Memcpy(void* Dest, const void* Src, size_t Count) { return std::memcpy(Dest, Src, Count); }
};
