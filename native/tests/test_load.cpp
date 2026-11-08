// GLB の物理データ読み込みと PmxPhysicsBuilder::Build の疎通確認 (Task 前の実測値と突き合わせる)。
#include "MmdGlbPhysicsReader.h"
#include "MmdPmxPhysicsBuilder.h"

#include <cstdio>
#include <cstdlib>
#include <string>

using namespace MmdPhysics;

static int Fail(const char* What, long Got, long Want)
{
	std::fprintf(stderr, "FAIL: %s = %ld (expected %ld)\n", What, Got, Want);
	return 1;
}

int main()
{
	const char* Env = std::getenv("MMD_TEST_GLB_SMALL");
	if (!Env || !*Env)
	{
		std::fprintf(stderr, "FAIL: environment variable MMD_TEST_GLB_SMALL is not set (see native/tests/local_env.example.sh)\n");
		return 1;
	}
	const std::string Path = Env;

	float UnitScale = 1.0f;
	TArray<FString> Warnings;
	TSharedPtr<PmxPhysicsModel> Model = GlbPhysicsReader::LoadFile(
		FString(MmdShim::Utf8ToUtf16(Path.data(), Path.size())), UnitScale, Warnings);
	if (!Model.IsValid())
	{
		std::fprintf(stderr, "FAIL: LoadFile returned null (%s)\n", Path.c_str());
		for (const FString& W : Warnings) std::fprintf(stderr, "  warn: %s\n", MmdShim::Utf16ToUtf8(*W, W.Len()).c_str());
		return 1;
	}
	for (const FString& W : Warnings) std::printf("warn: %s\n", MmdShim::Utf16ToUtf8(*W, W.Len()).c_str());
	std::printf("unitScale=%g bones=%d rigidBodies=%d joints=%d warnings=%d\n", UnitScale,
		Model->BoneNames.Num(), Model->RigidBodies.Num(), Model->Joints.Num(), Warnings.Num());

	if (Model->BoneNames.Num() != 179) return Fail("bones", Model->BoneNames.Num(), 179);
	if (Model->RigidBodies.Num() != 117) return Fail("rigid bodies", Model->RigidBodies.Num(), 117);
	if (Model->Joints.Num() != 165) return Fail("joints", Model->Joints.Num(), 165);

	TSharedPtr<PmxPhysicsBuilder> B = PmxPhysicsBuilder::Build(Model);
	if (!B.IsValid()) { std::fprintf(stderr, "FAIL: Build returned null\n"); return 1; }
	std::printf("built bodies=%d joints=%d\n", B->Bodies.Num(), B->World.Joints.Num());
	std::printf("OK\n");
	return 0;
}
