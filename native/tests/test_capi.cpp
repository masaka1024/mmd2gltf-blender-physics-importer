// C API のテスト (Task 7)。環境変数: MMD_TEST_GLB_SMALL (日本語パス), MMD_TEST_GLB_NOEXTRAS, MMD_TEST_GLB_NOANIM。
#include "mmdphys_capi.h"
#include "MmdGlbPhysicsReader.h"
#include "mmd_bake.h"
#include "mmd_fk.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

static int g_fail = 0;
#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); g_fail++; } } while (0)

static std::string Env(const char* k, const char* dflt)
{
	const char* v = std::getenv(k);
	return (v && *v) ? v : dflt;
}
static bool Has(const char* hay, const char* needle) { return std::strstr(hay, needle) != nullptr; }

int main()
{
	const std::string small = Env("MMD_TEST_GLB_SMALL", "/Users/masaka1024/Desktop/IA_Conqueror_v4のコピー.glb");
	const std::string noExtras = Env("MMD_TEST_GLB_NOEXTRAS", "/Users/masaka1024/KAIHATU/toyota-8fd25/out/toyota_8fd25.glb");
	const std::string noAnim = Env("MMD_TEST_GLB_NOANIM", "/Users/masaka1024/KAIHATU/zatsu_anime/src_f8/work/out.glb");

	CHECK(mmd_api_version() == 1);
	CHECK(mmd_last_error() != nullptr && mmd_last_error()[0] == '\0');

	// 存在しないパス
	CHECK(mmd_create("/nonexistent/dir/none.glb") == nullptr);
	CHECK(mmd_last_error()[0] != '\0');
	// NULL は例外にならず NULL + メッセージ
	CHECK(mmd_create(nullptr) == nullptr);
	CHECK(mmd_last_error()[0] != '\0');
	mmd_destroy(nullptr);

	// extras.mmd 無し
	CHECK(mmd_create(noExtras.c_str()) == nullptr);
	CHECK(Has(mmd_last_error(), "extras.mmd"));

	// アニメ無し
	{
		MmdHandle* h = mmd_create(noAnim.c_str());
		CHECK(h != nullptr);
		if (h)
		{
			MmdInfo info{};
			CHECK(mmd_get_info(h, &info) == MMD_OK);
			CHECK(info.frame_count == 0);
			CHECK(info.bone_count > 0);
			std::vector<float> buf(7);
			CHECK(mmd_bake(h, buf.data(), (int64_t)buf.size(), nullptr, nullptr) == MMD_ERR);
			CHECK(Has(mmd_last_error(), "animation"));
			mmd_destroy(h);
		}
	}

	// 日本語パス + 本体
	MmdHandle* h = mmd_create(small.c_str());
	CHECK(h != nullptr);
	if (!h) { std::fprintf(stderr, "last error: %s\n", mmd_last_error()); return 1; }
	CHECK(mmd_last_error()[0] == '\0');
	MmdInfo info{};
	CHECK(mmd_get_info(h, &info) == MMD_OK);
	CHECK(info.bone_count == 179);
	CHECK(info.physics_bone_count == 101);
	CHECK(info.frame_count == 7001);
	CHECK(info.unit_scale > 0.0f);
	const int nb = info.bone_count, nf = info.frame_count;

	std::vector<int32_t> pb(info.physics_bone_count);
	CHECK(mmd_get_physics_bones(h, pb.data(), (int32_t)pb.size()) == info.physics_bone_count);
	CHECK(mmd_get_physics_bones(h, pb.data(), 1) == MMD_ERR);
	CHECK(mmd_last_error()[0] != '\0');
	std::vector<float> rest((size_t)nb * 7), times(nf);
	CHECK(mmd_get_rest_world(h, rest.data(), nb) == nb);
	CHECK(mmd_get_rest_world(h, rest.data(), nb - 1) == MMD_ERR);
	CHECK(mmd_get_times(h, times.data(), nf) == nf);
	CHECK(mmd_get_times(h, times.data(), nf - 1) == MMD_ERR);

	// 容量不足
	{
		std::vector<float> small_buf(100);
		CHECK(mmd_bake(h, small_buf.data(), (int64_t)small_buf.size(), nullptr, nullptr) == MMD_ERR);
		CHECK(mmd_last_error()[0] != '\0');
	}

	// RunBake との全要素ビット一致
	{
		std::ifstream In(small, std::ios::binary);
		std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(In)), std::istreambuf_iterator<char>());
		mmdnative::GlbFile glb; std::string err;
		CHECK(mmdnative::LoadGlb(bytes, glb, err));
		mmdnative::Skeleton sk; CHECK(mmdnative::LoadSkeleton(glb, sk, err));
		mmdnative::Animation an; CHECK(mmdnative::LoadAnimation(glb, 0, an, err));
		float us = 0; TArray<FString> w;
		auto model = MmdPhysics::GlbPhysicsReader::LoadFile(FString(MmdShim::Utf8ToUtf16(small.data(), small.size())), us, w);
		CHECK(model.IsValid());
		const auto fk = mmdnative::ComputeFk(sk, an, us);
		const auto ref = mmdnative::RunBake(model, fk, an.Times, mmdnative::PlaybackSettings{}, nullptr);

		std::vector<float> out((size_t)nf * nb * 7);
		struct P { int calls = 0; int lastDone = 0; } prog;
		const int32_t r = mmd_bake(h, out.data(), (int64_t)out.size(),
			[](int32_t d, int32_t, void* u) { auto* p = static_cast<P*>(u); p->calls++; p->lastDone = d; }, &prog);
		CHECK(r == MMD_OK);
		CHECK(prog.calls > 0 && prog.lastDone == nf);
		long bad = 0;
		for (int f = 0; f < nf && ref.BoneWorld.size() == (size_t)nf; f++)
			for (int b = 0; b < nb; b++)
			{
				const auto& x = ref.BoneWorld[f][b];
				const float v[7] = {x.Origin.x, x.Origin.y, x.Origin.z, x.Rotation.x, x.Rotation.y, x.Rotation.z, x.Rotation.w};
				if (std::memcmp(v, &out[((size_t)f * nb + b) * 7], sizeof v) != 0) bad++;
			}
		CHECK(ref.BoneWorld.size() == (size_t)nf);
		CHECK(bad == 0);
		std::printf("bake compare: %ld mismatching bones\n", bad);

		// 先頭フレームの rest/times も検査
		CHECK(times[0] == an.Times[0]);
	}

	// ライブ: FK のフレーム 0..59 を入力に 60 回
	{
		std::ifstream In(small, std::ios::binary);
		std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(In)), std::istreambuf_iterator<char>());
		mmdnative::GlbFile glb; std::string err;
		mmdnative::LoadGlb(bytes, glb, err);
		mmdnative::Skeleton sk; mmdnative::LoadSkeleton(glb, sk, err);
		mmdnative::Animation an; mmdnative::LoadAnimation(glb, 0, an, err);
		const auto fk = mmdnative::ComputeFk(sk, an, info.unit_scale);
		CHECK(fk.size() >= 60);
		auto pack = [&](int f) {
			std::vector<float> v((size_t)nb * 7);
			for (int b = 0; b < nb; b++)
			{
				const auto& x = fk[f][b];
				const float t[7] = {x.Origin.x, x.Origin.y, x.Origin.z, x.Rotation.x, x.Rotation.y, x.Rotation.z, x.Rotation.w};
				std::memcpy(&v[(size_t)b * 7], t, sizeof t);
			}
			return v;
		};
		std::vector<float> out((size_t)nb * 7);
		// reset 前の step はエラー
		CHECK(mmd_live_step(h, pack(0).data(), 1.0f / 60, out.data()) == MMD_ERR);
		CHECK(mmd_live_reset(h, pack(0).data()) == MMD_OK);
		bool nan = false; int warn = 0; double moved = 0;
		std::vector<float> p0;
		for (int f = 0; f < 60; f++)
		{
			const auto in = pack(f);
			const int32_t r = mmd_live_step(h, in.data(), 1.0f / 60, out.data());
			if (r == MMD_WARN_NAN) warn++;
			CHECK(r == MMD_OK || r == MMD_WARN_NAN);
			for (float v : out) if (!std::isfinite(v)) nan = true;
			// 非物理ボーンは入力そのまま
			if (f == 59)
			{
				std::vector<bool> isPhys(nb, false);
				for (int32_t b : pb) isPhys[b] = true;
				for (int b = 0; b < nb; b++)
					if (!isPhys[b]) CHECK(std::memcmp(&out[(size_t)b * 7], &in[(size_t)b * 7], 28) == 0);
				for (int32_t b : pb) for (int c = 0; c < 3; c++) moved += std::fabs(out[(size_t)b * 7 + c] - in[(size_t)b * 7 + c]);
			}
		}
		CHECK(!nan);
		CHECK(warn == 0);
		std::printf("live: 60 steps, warn=%d, sum|phys-input| at f59 = %g\n", warn, moved);
		// reset し直せる
		CHECK(mmd_live_reset(h, pack(0).data()) == MMD_OK);
	}

	mmd_destroy(h);
	if (g_fail) { std::printf("FAIL (%d)\n", g_fail); return 1; }
	std::printf("PASS\n");
	return 0;
}
