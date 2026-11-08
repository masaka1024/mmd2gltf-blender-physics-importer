// 検証の層 3: FK のビット一致。
// physbake --dump-fk の CSV (frame,bone,px,py,pz,qx,qy,qz,qw / float32 ビット 8 桁 16 進) と
// mmdnative::ComputeFk の結果を全行 Hex8 の文字列一致 (= ビット一致) で比べる。
// usage: parity_fk <glb> <ref.fk.csv>
#include "MmdGlbPhysicsReader.h"
#include "csv_bits.h"
#include "mmd_fk.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

using namespace mmdnative;

static std::vector<std::string> Split(const std::string& L)
{
	std::vector<std::string> R;
	std::string Cur;
	for (char C : L)
	{
		if (C == ',') { R.push_back(Cur); Cur.clear(); }
		else if (C != '\r') Cur += C;
	}
	R.push_back(Cur);
	return R;
}

int main(int argc, char** argv)
{
	if (argc < 3) { std::fprintf(stderr, "usage: parity_fk <glb> <ref.fk.csv>\n"); return 2; }
	const std::string GlbPath = argv[1];
	if (GlbPath.empty()) { std::fprintf(stderr, "FAIL: test GLB path is empty (set MMD_TEST_GLB_SMALL / MMD_TEST_GLB before configuring; see native/tests/local_env.example.sh)\n"); return 1; }

	std::ifstream In(GlbPath, std::ios::binary);
	if (!In) { std::fprintf(stderr, "FAIL: cannot open %s\n", GlbPath.c_str()); return 1; }
	std::vector<uint8_t> Bytes((std::istreambuf_iterator<char>(In)), std::istreambuf_iterator<char>());

	GlbFile Glb; std::string Err;
	if (!LoadGlb(Bytes, Glb, Err)) { std::fprintf(stderr, "FAIL: LoadGlb: %s\n", Err.c_str()); return 1; }
	Skeleton Sk;
	if (!LoadSkeleton(Glb, Sk, Err)) { std::fprintf(stderr, "FAIL: LoadSkeleton: %s\n", Err.c_str()); return 1; }
	Animation Anim;
	if (!LoadAnimation(Glb, 0, Anim, Err)) { std::fprintf(stderr, "FAIL: LoadAnimation: %s\n", Err.c_str()); return 1; }

	// unitScale とバインド位置は physbake と同じく GlbPhysicsReader から (extras.mmd 欠落時は 1.0 に落ちる)。
	float UnitScale = 0.0f;
	TArray<FString> Warnings;
	TSharedPtr<MmdPhysics::PmxPhysicsModel> Model = MmdPhysics::GlbPhysicsReader::LoadFile(
		FString(MmdShim::Utf8ToUtf16(GlbPath.data(), GlbPath.size())), UnitScale, Warnings);
	if (!Model.IsValid()) { std::fprintf(stderr, "FAIL: GlbPhysicsReader::LoadFile\n"); return 1; }
	for (int i = 0; i < Warnings.Num(); i++) std::fprintf(stderr, "  warning(GLB): %s\n", ToUtf8(Warnings[i]).c_str());
	if (Model->BoneNames.Num() != Sk.BoneCount)
	{ std::fprintf(stderr, "FAIL: bone count %d vs skins %d\n", Model->BoneNames.Num(), Sk.BoneCount); return 1; }

	const float Worst = CheckBindPose(Sk, *Model, UnitScale);
	std::printf("bind pose max error %.3e (limit 1e-2)\n", static_cast<double>(Worst));
	if (!(Worst < 1e-2f)) { std::fprintf(stderr, "FAIL: CheckBindPose %g >= 1e-2\n", static_cast<double>(Worst)); return 1; }

	const auto World = ComputeFk(Sk, Anim, UnitScale);
	const int Frames = static_cast<int>(Anim.Times.size());
	if (static_cast<int>(World.size()) != Frames) { std::fprintf(stderr, "FAIL: ComputeFk returned %zu frames, expected %d\n", World.size(), Frames); return 1; }
	std::printf("unitScale %g  bones %d  frames %d\n", static_cast<double>(UnitScale), Sk.BoneCount, Frames);

	std::ifstream Csv(argv[2]);
	if (!Csv) { std::fprintf(stderr, "FAIL: cannot open %s\n", argv[2]); return 1; }
	std::string Line;
	std::getline(Csv, Line);
	if (Line != "frame,bone,px,py,pz,qx,qy,qz,qw") { std::fprintf(stderr, "FAIL: unexpected CSV header '%s'\n", Line.c_str()); return 1; }

	static const char* Cols[7] = {"px", "py", "pz", "qx", "qy", "qz", "qw"};
	long Rows = 0, Bad = 0;
	for (int f = 0; f < Frames; f++)
		for (int b = 0; b < Sk.BoneCount; b++)
		{
			if (!std::getline(Csv, Line)) { std::fprintf(stderr, "FAIL: CSV ended early at frame %d bone %d (rows %ld)\n", f, b, Rows); return 1; }
			const auto T = Split(Line);
			if (T.size() != 9 || std::atoi(T[0].c_str()) != f || std::atoi(T[1].c_str()) != b)
			{ std::fprintf(stderr, "FAIL: CSV row %ld malformed or out of order: '%s' (expected frame %d bone %d)\n", Rows, Line.c_str(), f, b); return 1; }
			const RigidTransform& X = World[f][b];
			const float V[7] = {X.Origin.x, X.Origin.y, X.Origin.z, X.Rotation.x, X.Rotation.y, X.Rotation.z, X.Rotation.w};
			Rows++;
			for (int c = 0; c < 7; c++)
			{
				const std::string H = Hex8(V[c]);
				if (H != T[2 + c])
				{
					if (Bad == 0)
						std::fprintf(stderr, "FIRST MISMATCH frame %d bone %d (%s) column %s: native %s vs ref %s\n",
						             f, b, Sk.Names[b].c_str(), Cols[c], H.c_str(), T[2 + c].c_str());
					Bad++;
				}
			}
		}
	if (std::getline(Csv, Line) && !Line.empty()) { std::fprintf(stderr, "FAIL: CSV has extra rows beyond %ld\n", Rows); return 1; }
	std::printf("compared %ld rows (%ld values), mismatched values: %ld\n", Rows, Rows * 7, Bad);
	if (Bad != 0) { std::printf("FAIL\n"); return 1; }
	std::printf("PASS\n");
	return 0;
}
