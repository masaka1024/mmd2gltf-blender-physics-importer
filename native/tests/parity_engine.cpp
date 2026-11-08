// 検証の層 2: エンジン単体のビット一致。
// UE5 版 MmdPhysicsGlbParityTest.cpp (Drive + Playback + 毎フレーム) と同じ手順を互換層の上で再現し、
// C# 参照エンジンが出した CSV と全フレーム・全剛体・7 値をビット単位で比べる。
// usage: parity_engine <ref.csv> [frames]   GLB は環境変数 MMD_TEST_GLB_SMALL
#include "MmdGlbPhysicsReader.h"
#include "MmdPmxPhysicsBuilder.h"
#include "csv_bits.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using namespace MmdPhysics;

namespace
{
	// UE5 版 ApplyPlaybackSettings / CsReference と同じ 7 項目。
	void ApplyPlaybackSettings(PhysicsWorld& W)
	{
		W.SolverIterations = 10;
		W.JointVelocityIterations = 40;
		W.JointMaxCorrectionVel = 30.0f;
		W.SubSteps = 2;
		W.FixedTimeStep = 1.0f / 60.0f;
		W.UseSplitImpulse = true;
		W.UseJointSplitImpulse = true;
	}

	constexpr float KDriveSwayAmp = 1.0f;
	constexpr float KDriveRotAmp = 0.2f;
	constexpr float KDriveTwoPi = 6.2831853f;

	void ApplyDrive(PmxPhysicsBuilder& B, const PmxPhysicsModel& Model, int32 Frame)
	{
		const float t = Frame / 30.0f;
		const float Sway = KDriveSwayAmp * MSin(KDriveTwoPi * 0.7f * t);
		const float Ang = KDriveRotAmp * MSin(KDriveTwoPi * 0.5f * t);
		const float Half = Ang * 0.5f;
		const Quat Q(0.0f, MSin(Half), 0.0f, MCos(Half));

		B.ApplyKinematicTargets([&Model, &Q, Sway](int32 i) -> TOptional<RigidTransform>
		{
			if (i < 0 || i >= Model.BonePositions.Num()) return TOptional<RigidTransform>();
			const Vec3& P = Model.BonePositions[i];
			return TOptional<RigidTransform>(RigidTransform(Q, Vec3(P.x + Sway, P.y, P.z)));
		});
	}

	struct Row
	{
		int Frame = 0;
		std::string Name;
		float V[7] = {};   // px py pz qx qy qz qw
	};

	std::vector<std::string> Split(const std::string& L)
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

	bool LoadCsv(const char* Path, std::vector<Row>& Out)
	{
		std::ifstream F(Path);
		if (!F) { std::fprintf(stderr, "FAIL: cannot open %s\n", Path); return false; }
		std::string Line;
		std::getline(F, Line);
		if (Line.rfind("frame,", 0) != 0) { std::fprintf(stderr, "FAIL: not a per-frame CSV\n"); return false; }
		int N = 1;
		while (std::getline(F, Line))
		{
			++N;
			if (Line.empty()) continue;
			std::vector<std::string> C = Split(Line);
			if (C.size() != 10) { std::fprintf(stderr, "FAIL: line %d has %zu cols\n", N, C.size()); return false; }
			Row R;
			R.Frame = std::atoi(C[0].c_str());
			R.Name = C[2];
			for (int k = 0; k < 7; k++)
			{
				if (!ParseRoundTripFloat(C[3 + k], R.V[k]))
				{
					std::fprintf(stderr, "FAIL: line %d col %d: bad float '%s'\n", N, 3 + k, C[3 + k].c_str());
					return false;
				}
			}
			Out.push_back(std::move(R));
		}
		return !Out.empty();
	}
}

int main(int argc, char** argv)
{
	if (argc < 2) { std::fprintf(stderr, "usage: parity_engine <ref.csv> [frames]\n"); return 2; }
	const char* Env = std::getenv("MMD_TEST_GLB_SMALL");
	if (!Env || !*Env)
	{
		std::fprintf(stderr, "FAIL: environment variable MMD_TEST_GLB_SMALL is not set (see native/tests/local_env.example.sh)\n");
		return 1;
	}
	const std::string GlbPath = Env;
	const int Frames = argc > 2 ? std::atoi(argv[2]) : 300;

	std::vector<Row> Golden;
	if (!LoadCsv(argv[1], Golden)) return 1;

	float UnitScale = 0.0f;
	TArray<FString> Warnings;
	TSharedPtr<PmxPhysicsModel> Model = GlbPhysicsReader::LoadFile(
		FString(MmdShim::Utf8ToUtf16(GlbPath.data(), GlbPath.size())), UnitScale, Warnings);
	if (!Model.IsValid()) { std::fprintf(stderr, "FAIL: LoadFile (%s)\n", GlbPath.c_str()); return 1; }
	TSharedPtr<PmxPhysicsBuilder> B = PmxPhysicsBuilder::Build(Model);
	if (!B.IsValid()) { std::fprintf(stderr, "FAIL: Build\n"); return 1; }
	ApplyPlaybackSettings(B->World);

	const size_t NB = static_cast<size_t>(B->Bodies.Num());
	if (Golden.size() != NB * static_cast<size_t>(Frames))
	{
		std::fprintf(stderr, "FAIL: CSV rows %zu != bodies %zu x frames %d\n", Golden.size(), NB, Frames);
		return 1;
	}

	static const char* Cols[7] = {"px", "py", "pz", "qx", "qy", "qz", "qw"};
	long Mismatches = 0, Compared = 0;
	double MaxDiff = 0.0;
	bool FirstReported = false;

	for (int f = 0; f < Frames; f++)
	{
		ApplyDrive(*B, *Model, f + 1);
		B->World.StepSimulation(1.0f / 30.0f);
		for (size_t i = 0; i < NB; i++)
		{
			const Row& G = Golden[static_cast<size_t>(f) * NB + i];
			const RigidTransform& T = B->Bodies[static_cast<int32>(i)]->WorldTransform;
			const float Got[7] = {T.Origin.x, T.Origin.y, T.Origin.z, T.Rotation.x, T.Rotation.y, T.Rotation.z, T.Rotation.w};
			std::string Name = MmdShim::Utf16ToUtf8(*B->Bodies[static_cast<int32>(i)]->Name, B->Bodies[static_cast<int32>(i)]->Name.Len());
			for (char& C : Name) if (C == ',') C = '_';
			if (G.Frame != f + 1 || G.Name != Name)
			{
				std::fprintf(stderr, "FAIL: row mismatch frame %d body %zu: csv(frame=%d name=%s) vs engine(%s)\n",
					f + 1, i, G.Frame, G.Name.c_str(), Name.c_str());
				return 1;
			}
			for (int k = 0; k < 7; k++)
			{
				++Compared;
				if (FloatBits(Got[k]) == FloatBits(G.V[k])) continue;
				++Mismatches;
				const double D = std::fabs(static_cast<double>(Got[k]) - static_cast<double>(G.V[k]));
				if (D > MaxDiff) MaxDiff = D;
				if (!FirstReported)
				{
					FirstReported = true;
					std::fprintf(stderr, "FIRST DIVERGENCE: frame %d body #%zu '%s' %s: C++ %.9g (0x%s) vs C# %.9g (0x%s)\n",
						f + 1, i, Name.c_str(), Cols[k], Got[k], Hex8(Got[k]).c_str(), G.V[k], Hex8(G.V[k]).c_str());
				}
			}
		}
	}

	std::printf("compared %ld values (%d frames x %zu bodies x 7), mismatches=%ld, max abs diff=%g\n",
		Compared, Frames, NB, Mismatches, MaxDiff);
	if (Mismatches) { std::printf("FAIL\n"); return 1; }
	std::printf("PASS (bit-identical)\n");
	return 0;
}
