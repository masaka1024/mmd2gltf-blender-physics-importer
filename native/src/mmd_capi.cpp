// C 言語の窓口。例外は外へ出さず、エラーは mmd_last_error() (スレッドごと・UTF-8) に入れる。
#include "mmdphys_capi.h"

#include "MmdGlbPhysicsReader.h"
#include "mmd_bake.h"
#include "mmd_fk.h"
#include "mmd_glb.h"

#include <cstdio>
#include <cstring>
#include <exception>
#include <memory>
#include <string>
#include <vector>

#if defined(_WIN32)
#  include "mmd_win_file.h"   // <windows.h> は shim の TCHAR と衝突するのでここでは include しない
#endif

using namespace mmdnative;
using namespace MmdPhysics;

namespace
{
	thread_local std::string g_error;
	// g_error への代入が確保に失敗したとき (bad_alloc 等) に返す静的文字列。
	thread_local const char* g_errorFallback = nullptr;

	// 例外を出さない。確保に失敗したら静的な文言へ切り替える。
	void SetError(const std::string& s) noexcept
	{
		try { g_error = s; g_errorFallback = nullptr; }
		catch (...) { g_errorFallback = "out of memory while recording error"; }
	}
	// catch ブロック用: 文字列の組み立ても noexcept 内で行う。
	void SetErrorWhat(const char* prefix, const char* what) noexcept
	{
		try { g_error.assign(prefix); g_error.append(what ? what : ""); g_errorFallback = nullptr; }
		catch (...) { g_errorFallback = "out of memory"; }
	}
	void ClearError() noexcept { g_errorFallback = nullptr; g_error.clear(); }

	// UTF-8 パスでファイルを読む (Windows は UTF-8 -> UTF-16 に変換して _wfopen)。
	bool ReadFileBytes(const char* utf8Path, std::vector<uint8_t>& out, std::string& err)
	{
		FILE* fp = nullptr;
#if defined(_WIN32)
		bool invalidUtf8 = false;
		fp = OpenFileForReadUtf8(utf8Path, invalidUtf8);
		if (invalidUtf8) { err = std::string("path is not valid UTF-8: ") + utf8Path; return false; }
#else
		fp = std::fopen(utf8Path, "rb");
#endif
		if (!fp) { err = std::string("cannot open file: ") + utf8Path; return false; }
		out.clear();
		uint8_t buf[1 << 16];
		size_t got;
		while ((got = std::fread(buf, 1, sizeof buf, fp)) > 0) out.insert(out.end(), buf, buf + got);
		const bool bad = std::ferror(fp) != 0;
		std::fclose(fp);
		if (bad) { err = std::string("read error: ") + utf8Path; return false; }
		return true;
	}

	void Pack(const RigidTransform& x, float* o)
	{
		o[0] = x.Origin.x; o[1] = x.Origin.y; o[2] = x.Origin.z;
		o[3] = x.Rotation.x; o[4] = x.Rotation.y; o[5] = x.Rotation.z; o[6] = x.Rotation.w;
	}
	RigidTransform Unpack(const float* p)
	{
		return RigidTransform(Quat(p[3], p[4], p[5], p[6]), Vec3(p[0], p[1], p[2]));
	}
}

struct MmdHandle
{
	GlbFile Glb;
	Skeleton Sk;
	Animation Anim;
	bool HasAnim = false;
	float UnitScale = 1.0f;
	std::shared_ptr<PmxPhysicsModel> Model;
	std::vector<int> PhysicsBones;
	std::vector<RigidTransform> RestWorld;
	PlaybackSettings Settings;

	// ライブ
	TSharedPtr<PmxPhysicsBuilder> Live;
	bool NeedAligned = false;
	std::vector<RigidTransform> LastOut;   // 直近の出力 (NaN 時に保持)
};

namespace
{
	bool CheckHandle(MmdHandle* h)
	{
		if (!h) { SetError("handle is null"); return false; }
		return true;
	}

	// handle 引数の関数を try/catch で包む共通処理。
	template <class F>
	int32_t Guard(F&& f)
	{
		try { return f(); }
		catch (const std::exception& e) { SetErrorWhat("exception: ", e.what()); }
		catch (...) { SetErrorWhat("unknown exception", ""); }
		return MMD_ERR;
	}
}

extern "C"
{
MMD_API int32_t mmd_api_version(void) { return MMD_API_VERSION; }

MMD_API const char* mmd_last_error(void) { return g_errorFallback ? g_errorFallback : g_error.c_str(); }

MMD_API MmdHandle* mmd_create(const char* utf8_glb_path)
{
	ClearError();
	try
	{
		if (!utf8_glb_path) { SetError("path is null"); return nullptr; }
		std::vector<uint8_t> bytes;
		std::string err;
		if (!ReadFileBytes(utf8_glb_path, bytes, err)) { SetError(err); return nullptr; }

		auto h = std::make_unique<MmdHandle>();
		if (!LoadGlb(bytes, h->Glb, err)) { SetError("LoadGlb: " + err); return nullptr; }

		// extras.mmd が無い GLB は物理情報が無いので受け付けない。
		{
			auto extras = JsonGet(h->Glb.Json, "extras");
			auto mmd = extras.IsValid() ? JsonGet(extras, "mmd") : TSharedPtr<MmdPhysics::MmdJsonValue>();
			if (!mmd.IsValid()) { SetError("GLB has no extras.mmd (physics data missing)"); return nullptr; }
		}

		TArray<FString> warnings;
		TArray<uint8> tbytes;
		tbytes.SetNum(static_cast<int32>(bytes.size()));
		if (!bytes.empty()) std::memcpy(tbytes.GetData(), bytes.data(), bytes.size());
		h->Model = MmdPhysics::GlbPhysicsReader::LoadBytes(tbytes, h->UnitScale, warnings);
		if (!h->Model)
		{
			std::string m = "failed to read physics model";
			for (int32 i = 0; i < warnings.Num(); i++) m += "; " + ToUtf8(warnings[i]);
			SetError(m);
			return nullptr;
		}

		if (!LoadSkeleton(h->Glb, h->Sk, err)) { SetError("LoadSkeleton: " + err); return nullptr; }
		const int nb = h->Sk.BoneCount;
		if (h->Model->BoneNames.Num() != nb)
		{
			SetError("bone count mismatch: model " + std::to_string(h->Model->BoneNames.Num()) +
			         " vs skin " + std::to_string(nb));
			return nullptr;
		}
		const std::vector<int> order = h->Sk.TopologicalOrder();
		if (static_cast<int>(order.size()) != nb)
		{ SetError("bone hierarchy is cyclic"); return nullptr; }

		// rest world (アニメ無し、バインド姿勢の FK 合成)
		h->RestWorld.assign(nb, RigidTransform());
		for (int b : order)
		{
			const RigidTransform local = GltfToMmd(h->Sk.RestPos[b], h->Sk.RestRot[b], h->UnitScale);
			const int p = h->Sk.ParentBone[b];
			h->RestWorld[b] = p >= 0 ? h->RestWorld[p] * local : local;
		}

		// アニメ: animations[0] のみ。0 本なら frame_count = 0。
		const FMmdJsonArray* anims = GlbArr(h->Glb, "animations");
		if (anims && anims->Num() > 0)
		{
			if (!LoadAnimation(h->Glb, 0, h->Anim, err)) { SetError("LoadAnimation: " + err); return nullptr; }
			h->HasAnim = true;
		}

		// 物理ボーン一覧 (ビルダは捨てる。ライブは reset で作り直す)
		TSharedPtr<PmxPhysicsBuilder> b = PmxPhysicsBuilder::Build(TSharedPtr<PmxPhysicsModel>(h->Model));
		if (!b.IsValid()) { SetError("PmxPhysicsBuilder::Build failed"); return nullptr; }
		h->PhysicsBones = CollectPhysicsBones(*b, nb);
		return h.release();
	}
	catch (const std::exception& e) { SetErrorWhat("exception: ", e.what()); }
	catch (...) { SetErrorWhat("unknown exception", ""); }
	return nullptr;
}

MMD_API void mmd_destroy(MmdHandle* h)
{
	ClearError();
	try { delete h; } catch (...) {}
}

MMD_API int32_t mmd_get_info(MmdHandle* h, MmdInfo* out)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		if (!out) { SetError("out is null"); return MMD_ERR; }
		out->bone_count = h->Sk.BoneCount;
		out->physics_bone_count = static_cast<int32_t>(h->PhysicsBones.size());
		out->frame_count = h->HasAnim ? static_cast<int32_t>(h->Anim.Times.size()) : 0;
		out->unit_scale = h->UnitScale;
		return MMD_OK;
	});
}

MMD_API int32_t mmd_get_physics_bones(MmdHandle* h, int32_t* out, int32_t cap)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		const int32_t n = static_cast<int32_t>(h->PhysicsBones.size());
		if (n > 0 && !out) { SetError("out is null"); return MMD_ERR; }
		if (cap < n) { SetError("buffer too small: need " + std::to_string(n) + " ints"); return MMD_ERR; }
		for (int32_t i = 0; i < n; i++) out[i] = h->PhysicsBones[i];
		return n;
	});
}

MMD_API int32_t mmd_get_rest_world(MmdHandle* h, float* out, int32_t cap_bones)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		const int32_t n = h->Sk.BoneCount;
		if (n > 0 && !out) { SetError("out is null"); return MMD_ERR; }
		if (cap_bones < n) { SetError("buffer too small: need " + std::to_string(n) + " bones"); return MMD_ERR; }
		for (int32_t b = 0; b < n; b++) Pack(h->RestWorld[b], out + static_cast<size_t>(b) * 7);
		return n;
	});
}

MMD_API int32_t mmd_get_times(MmdHandle* h, float* out, int32_t cap_frames)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		const int32_t n = h->HasAnim ? static_cast<int32_t>(h->Anim.Times.size()) : 0;
		if (n > 0 && !out) { SetError("out is null"); return MMD_ERR; }
		if (cap_frames < n) { SetError("buffer too small: need " + std::to_string(n) + " frames"); return MMD_ERR; }
		for (int32_t i = 0; i < n; i++) out[i] = h->Anim.Times[i];
		return n;
	});
}

MMD_API int32_t mmd_bake(MmdHandle* h, float* out_world, int64_t cap_floats, MmdProgressFn progress, void* user)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		if (!h->HasAnim) { SetError("GLB has no animation (animations[0] is required for baking)"); return MMD_ERR; }
		const int nb = h->Sk.BoneCount;
		const int frames = static_cast<int>(h->Anim.Times.size());
		const int64_t need = static_cast<int64_t>(frames) * nb * 7;
		if (!out_world && need > 0) { SetError("out_world is null"); return MMD_ERR; }
		if (cap_floats < need)
		{ SetError("buffer too small: need " + std::to_string(need) + " floats"); return MMD_ERR; }

		const auto fk = ComputeFk(h->Sk, h->Anim, h->UnitScale);
		if (fk.empty()) { SetError("FK failed (cyclic bone hierarchy or no frames)"); return MMD_ERR; }

		std::function<void(int, int)> cb;
		if (progress) cb = [&](int f, int total) { progress(static_cast<int32_t>(f + 1), static_cast<int32_t>(total), user); };
		const BakeResult r = RunBake(h->Model, fk, h->Anim.Times, h->Settings, cb);
		if (!r.Error.empty()) { SetError("bake failed: " + r.Error); return MMD_ERR; }
		if (static_cast<int>(r.BoneWorld.size()) != frames) { SetError("bake returned wrong frame count"); return MMD_ERR; }
		for (int f = 0; f < frames; f++)
			for (int b = 0; b < nb; b++)
				Pack(r.BoneWorld[f][b], out_world + (static_cast<size_t>(f) * nb + b) * 7);
		if (r.NanSeen)
		{
			SetError("physics diverged (NaN) first at bone: " + r.NanBone);
			return MMD_WARN_NAN;
		}
		return MMD_OK;
	});
}

MMD_API int32_t mmd_live_reset(MmdHandle* h, const float* in)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		h->Live = TSharedPtr<PmxPhysicsBuilder>();   // 失敗したら「reset が必要」状態に戻る
		if (!in) { SetError("bone_world_in is null"); return MMD_ERR; }
		const int nb = h->Sk.BoneCount;
		TSharedPtr<PmxPhysicsBuilder> live = PmxPhysicsBuilder::Build(TSharedPtr<PmxPhysicsModel>(h->Model));
		if (!live.IsValid()) { SetError("PmxPhysicsBuilder::Build failed"); return MMD_ERR; }
		PmxPhysicsBuilder& builder = *live;
		ApplySolverSettings(builder.World, h->Settings);
		h->NeedAligned = h->Settings.AlignBonePositions ||
		                 (h->Settings.BoneMergeMode && builder.HasBoneMergeBodies());
		auto getter = [in, nb](int32 bi) -> TOptional<RigidTransform>
		{
			if (bi >= 0 && bi < nb) return TOptional<RigidTransform>(Unpack(in + static_cast<size_t>(bi) * 7));
			return TOptional<RigidTransform>();
		};
		builder.ApplyKinematicTargets(getter);
		builder.ResetBodiesToBonePoseFk(getter);
		h->LastOut.resize(nb);
		for (int b = 0; b < nb; b++) h->LastOut[b] = Unpack(in + static_cast<size_t>(b) * 7);
		h->Live = live;   // 最後に公開
		return MMD_OK;
	});
}

MMD_API int32_t mmd_live_step(MmdHandle* h, const float* in, float dt, float* out)
{
	ClearError();
	return Guard([&]() -> int32_t {
		if (!CheckHandle(h)) return MMD_ERR;
		if (!h->Live.IsValid()) { SetError("mmd_live_reset must be called before mmd_live_step"); return MMD_ERR; }
		if (!in || !out) { SetError("bone_world_in / bone_world_out is null"); return MMD_ERR; }
		const int nb = h->Sk.BoneCount;
		PmxPhysicsBuilder& builder = *h->Live;
		auto getter = [in, nb](int32 bi) -> TOptional<RigidTransform>
		{
			if (bi >= 0 && bi < nb) return TOptional<RigidTransform>(Unpack(in + static_cast<size_t>(bi) * 7));
			return TOptional<RigidTransform>();
		};
		builder.ApplyKinematicTargets(getter);
		if (!(dt > 0.0f)) dt = builder.World.FixedTimeStep;
		builder.World.StepSimulation(dt);

		TArray<TOptional<RigidTransform>> aligned;
		if (h->NeedAligned)
			aligned = builder.ComputeAlignedBonePoses(getter, h->Settings.AlignRotClampAlpha, h->Settings.AlignBonePositions);

		std::memmove(out, in, static_cast<size_t>(nb) * 7 * sizeof(float));   // out == in を許す
		bool nan = false;
		std::string nanBone;
		for (const BoneLink& link : builder.BoneLinks)
		{
			if (link.Mode == EPhysicsMode::BoneFollow) continue;
			const int bi = link.BoneIndex;
			if (bi < 0 || bi >= nb) continue;
			RigidTransform w = ResolvePhysicsBoneWorld(link, aligned, h->NeedAligned, h->Settings);
			if (!IsFiniteTransform(w))
			{
				if (!nan)
				{
					nanBone = bi < static_cast<int>(h->Sk.Names.size()) ? h->Sk.Names[bi] : std::string();
					if (nanBone.empty()) nanBone = "#" + std::to_string(bi);
				}
				nan = true;
				w = h->LastOut[bi];
			}
			h->LastOut[bi] = w;
			Pack(w, out + static_cast<size_t>(bi) * 7);
		}
		// 非物理ボーンは入力をそのまま保持 (次回 NaN 時の基準としても使えるよう更新はしない)
		if (nan)
		{
			SetError("physics diverged (NaN) first at bone: " + nanBone + "; previous pose kept for affected bones");
			return MMD_WARN_NAN;
		}
		return MMD_OK;
	});
}
}
