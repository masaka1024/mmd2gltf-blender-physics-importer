// LoadSkeleton の JSON 数値読み: ロケール非依存・不正トークン拒否。GLB 不要 (JSON チャンクだけの最小 GLB を作る)。
#include "mmd_fk.h"

#include <clocale>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

using namespace mmdnative;

static std::vector<uint8_t> MakeGlb(const std::string& translation)
{
	std::string j = "{\"nodes\":[{\"name\":\"a\",\"translation\":" + translation + "}],\"skins\":[{\"joints\":[0]}]}";
	while (j.size() % 4) j += ' ';
	std::vector<uint8_t> b(20 + j.size());
	auto W = [&](size_t o, uint32_t v) { std::memcpy(b.data() + o, &v, 4); };
	W(0, 0x46546C67); W(4, 2); W(8, static_cast<uint32_t>(b.size()));
	W(12, static_cast<uint32_t>(j.size())); W(16, 0x4E4F534A);
	std::memcpy(b.data() + 20, j.data(), j.size());
	return b;
}

static int Fail(const char* m) { std::fprintf(stderr, "FAIL: %s\n", m); return 1; }

int main()
{
	GlbFile G; Skeleton S; std::string E;
	if (!LoadGlb(MakeGlb("[0.5,-1.25,2.0]"), G, E) || !LoadSkeleton(G, S, E)) return Fail("baseline load");
	if (S.RestPos[0].x != 0.5f || S.RestPos[0].y != -1.25f || S.RestPos[0].z != 2.0f) return Fail("baseline values");

	const char* Loc = std::setlocale(LC_NUMERIC, "de_DE.UTF-8");
	if (!Loc) Loc = std::setlocale(LC_NUMERIC, "de_DE");
	if (Loc)
	{
		char Buf[32]; std::snprintf(Buf, sizeof(Buf), "%.1f", 0.5);
		std::printf("locale %s (printf 0.5 -> %s)\n", Loc, Buf);
		GlbFile G2; Skeleton S2;
		if (!LoadGlb(MakeGlb("[0.5,-1.25,2.0]"), G2, E) || !LoadSkeleton(G2, S2, E)) return Fail("load under de_DE");
		if (S2.RestPos[0].x != 0.5f || S2.RestPos[0].y != -1.25f) return Fail("values differ under de_DE locale");
		std::setlocale(LC_NUMERIC, "C");
	}
	else std::printf("de_DE locale unavailable: locale part skipped\n");

	for (const char* Bad : {"[1.0.0,2,3]", "[1e999,2,3]", "[--1,2,3]"})
	{
		GlbFile G3; Skeleton S3;
		if (!LoadGlb(MakeGlb(Bad), G3, E)) return Fail("LoadGlb of bad-token GLB");
		if (LoadSkeleton(G3, S3, E)) { std::fprintf(stderr, "FAIL: malformed token accepted: %s\n", Bad); return 1; }
	}
	std::printf("PASS\n");
	return 0;
}
