// FMath 互換の ±0 / 特殊値の挙動を UE 5.8 の式と突き合わせる (ビットパターンで比較)。
//   Max(A,B) = (B < A) ? A : B   Min(A,B) = (A < B) ? A : B   Clamp = Max(Min(X,Hi),Lo)
//   Abs(float)=fabsf, Abs(double)=fabs (GenericPlatformMath.h:1346-1355)    Atan2(double): X==0 && Y==0 -> +0.0
#include "CoreMinimal.h"

#include <cstdio>

static int Failures = 0;

static uint32 Bits(float f) { uint32 b; std::memcpy(&b, &f, 4); return b; }
static uint64 Bits(double f) { uint64 b; std::memcpy(&b, &f, 8); return b; }
static float Rt(float v) { volatile float x = v; return x; }

#define EXPECT_BITS(expr, want) do { auto got_ = (expr); auto want_ = (want); if (Bits(got_) != Bits(want_)) { \
	std::fprintf(stderr, "FAIL line %d: %s -> bits %llx, want %llx\n", __LINE__, #expr, (unsigned long long)Bits(got_), (unsigned long long)Bits(want_)); Failures++; } } while (0)

int main()
{
	// ±0 は実行時の値で測る: MSVC の /O2 はインライン展開後に両辺が定数の ±0 だと max/min を畳み込んで逆側を返す
	// (/Od・/fp:strict・実行時の値では正しい)。エンジンが実行する演算の検査なので Rt() で定数畳み込みを止める。
	// Max: (B < A) ? A : B  -- 等しい (±0) ときは B
	EXPECT_BITS(FMath::Max(Rt(0.0f), Rt(-0.0f)), -0.0f);
	EXPECT_BITS(FMath::Max(Rt(-0.0f), Rt(0.0f)), 0.0f);
	// Min: (A < B) ? A : B  -- 等しい (±0) ときは B
	EXPECT_BITS(FMath::Min(Rt(0.0f), Rt(-0.0f)), -0.0f);
	EXPECT_BITS(FMath::Min(Rt(-0.0f), Rt(0.0f)), 0.0f);
	EXPECT_BITS(FMath::Max(1.0f, 2.0f), 2.0f);
	EXPECT_BITS(FMath::Min(1.0f, 2.0f), 1.0f);
	// Clamp = Max(Min(X,Hi),Lo)
	EXPECT_BITS(FMath::Clamp(Rt(-0.0f), Rt(0.0f), 1.0f), 0.0f);
	EXPECT_BITS(FMath::Clamp(Rt(0.0f), Rt(-0.0f), 1.0f), -0.0f);
	EXPECT_BITS(FMath::Clamp(5.0f, 0.0f, 1.0f), 1.0f);
	EXPECT_BITS(FMath::Clamp(-5.0f, -1.0f, 1.0f), -1.0f);
	// Abs(float/double) は fabsf/fabs なので -0.0 は +0.0 になる
	EXPECT_BITS(FMath::Abs(-0.0f), 0.0f);
	EXPECT_BITS(FMath::Abs(-2.5f), 2.5f);
	EXPECT_BITS(FMath::Abs<float>(-0.0f), 0.0f);
	EXPECT_BITS(FMath::Abs<double>(-0.0), 0.0);
	EXPECT_BITS(FMath::Abs(-0.0), 0.0);
	// Atan2(double): 0/0 は +0.0 (素の atan2(-0,-0) は -pi)
	EXPECT_BITS(FMath::Atan2(0.0, 0.0), 0.0);
	EXPECT_BITS(FMath::Atan2(-0.0, -0.0), 0.0);
	EXPECT_BITS(FMath::Atan2(0.0, -0.0), 0.0);
	EXPECT_BITS(FMath::Atan2(1.0, 0.0), std::atan2(1.0, 0.0));
	// Acos/Asin は [-1,1] に丸める
	EXPECT_BITS(FMath::Acos(1.5f), 0.0f);
	EXPECT_BITS(FMath::Asin(2.0f), asinf(1.0f));
	// RoundHalfToEven
	EXPECT_BITS(FMath::RoundHalfToEven(0.5), 0.0);
	EXPECT_BITS(FMath::RoundHalfToEven(1.5), 2.0);
	EXPECT_BITS(FMath::RoundHalfToEven(2.5), 2.0);
	EXPECT_BITS(FMath::RoundHalfToEven(-1.5), -2.0);
	EXPECT_BITS(FMath::RoundHalfToEven(-2.5), -2.0);
	EXPECT_BITS(FMath::RoundHalfToEven(3.2), 3.0);
	EXPECT_BITS(FMath::RoundHalfToEven(-3.7f), -4.0f);

	if (Failures == 0) std::printf("OK\n");
	return Failures == 0 ? 0 : 1;
}
