// 基本型・マクロ・FMath・FMemory (UE5 互換)。
#pragma once

#include <cmath>
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

template <typename T> struct TNumericLimits : std::numeric_limits<T>
{
	static constexpr T Max() noexcept { return std::numeric_limits<T>::max(); }
	static constexpr T Min() noexcept { return std::numeric_limits<T>::min(); }
	static constexpr T Lowest() noexcept { return std::numeric_limits<T>::lowest(); }
};

// FMath: 標準ライブラリの同名関数を直接呼ぶ。UE の定義が素の std 呼びと違う所だけ UE に合わせる:
//  * Max/Min : Max(A,B) = (A >= B) ? A : B,  Min(A,B) = (A <= B) ? A : B  (NaN・符号付きゼロの挙動が std::max/min と違う)
//  * Clamp   : X < Min ? Min : X < Max ? X : Max
//  * Acos/Asin : UE は引数を [-1,1] に丸めてから acos/asin を呼ぶ
//  * Abs(float/double) : fabs 系
//  * Atan2(float,float) : UE は多項式近似を使うので、ここでは delete して誤用を防ぐ (double 版は std::atan2)
class FMath
{
public:
	template <typename T> static constexpr FORCEINLINE T Max(const T A, const T B) { return (A >= B) ? A : B; }
	template <typename T> static constexpr FORCEINLINE T Min(const T A, const T B) { return (A <= B) ? A : B; }
	template <typename T> static constexpr FORCEINLINE T Clamp(const T X, const T Min_, const T Max_) { return X < Min_ ? Min_ : X < Max_ ? X : Max_; }
	template <typename T> static constexpr FORCEINLINE T Abs(const T A) { return (A >= static_cast<T>(0)) ? A : -A; }
	static FORCEINLINE float Abs(const float A) { return std::fabs(A); }
	static FORCEINLINE double Abs(const double A) { return std::fabs(A); }

	static FORCEINLINE float Sqrt(float V) { return std::sqrt(V); }
	static FORCEINLINE double Sqrt(double V) { return std::sqrt(V); }
	static FORCEINLINE float Sin(float V) { return std::sin(V); }
	static FORCEINLINE double Sin(double V) { return std::sin(V); }
	static FORCEINLINE float Cos(float V) { return std::cos(V); }
	static FORCEINLINE double Cos(double V) { return std::cos(V); }
	static FORCEINLINE float Atan(float V) { return std::atan(V); }
	static FORCEINLINE double Atan(double V) { return std::atan(V); }
	static FORCEINLINE double Atan2(double Y, double X) { return std::atan2(Y, X); }
	static float Atan2(float Y, float X) = delete;
	static FORCEINLINE float Acos(float V) { return std::acos((V < -1.f) ? -1.f : ((V < 1.f) ? V : 1.f)); }
	static FORCEINLINE double Acos(double V) { return std::acos((V < -1.0) ? -1.0 : ((V < 1.0) ? V : 1.0)); }
	static FORCEINLINE float Asin(float V) { return std::asin((V < -1.f) ? -1.f : ((V < 1.f) ? V : 1.f)); }
	static FORCEINLINE double Asin(double V) { return std::asin((V < -1.0) ? -1.0 : ((V < 1.0) ? V : 1.0)); }
	static FORCEINLINE float Pow(float A, float B) { return std::pow(A, B); }
	static FORCEINLINE double Pow(double A, double B) { return std::pow(A, B); }
	// 既定の丸めモード (最近接偶数) の nearbyint = 偶数丸め。
	static FORCEINLINE float RoundHalfToEven(float F) { return std::nearbyint(F); }
	static FORCEINLINE double RoundHalfToEven(double F) { return std::nearbyint(F); }
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
