#include "mmd_fk.h"

#include <algorithm>
#include <cmath>
#include <set>

namespace mmdnative
{
	using namespace MmdPhysics;

	namespace
	{
		// 移植元 GltfSkeleton.cs:81-84 (ReadVec3)
		Vec3 ReadVec3(bool has, const std::vector<float>& a)
		{
			return (has && a.size() >= 3) ? Vec3(a[0], a[1], a[2]) : Vec3::Zero;
		}
		// 移植元 GltfSkeleton.cs:86-90 (ReadQuat)
		Quat ReadQuat(bool has, const std::vector<float>& a)
		{
			return (has && a.size() >= 4) ? Quat(a[0], a[1], a[2], a[3]) : Quat::Identity;
		}
	}

	// 移植元 GltfSkeleton.cs:30-79 (Load)
	bool LoadSkeleton(const GlbFile& glb, Skeleton& sk, std::string& err)
	{
		const FMmdJsonArray* nodes = GlbArr(glb, "nodes");
		if (!nodes) { err = "glTF has no 'nodes'"; return false; }
		const FMmdJsonArray* skins = GlbArr(glb, "skins");
		if (!skins || skins->Num() == 0) { err = "glTF has no skins"; return false; }
		const FMmdJsonArray* joints = MiniJson::Arr(JsonGet((*skins)[0], "joints"));
		if (!joints) { err = "skins[0].joints missing"; return false; }

		// rest 値はテキストから直接 float に丸める (System.Text.Json の GetValue<float> と同じ。mmd_glb.h 参照)。
		std::vector<NodeFloats> nf;
		if (!ReadNodeFloats(glb, nf, err)) return false;
		if (static_cast<int>(nf.size()) != nodes->Num()) { err = "node count mismatch between JSON scans"; return false; }

		const int nb = joints->Num();
		sk = Skeleton();
		sk.BoneCount = nb;
		sk.NodeOfBone.assign(nb, 0);
		sk.BoneOfNode.assign(nodes->Num(), -1);
		sk.ParentBone.assign(nb, -1);
		sk.RestPos.assign(nb, Vec3());
		sk.RestRot.assign(nb, Quat());
		sk.Names.assign(nb, std::string());
		for (int b = 0; b < nb; b++)
		{
			const int ni = MiniJson::Int((*joints)[b]);
			if (ni < 0 || ni >= nodes->Num())
			{ err = "bone " + std::to_string(b) + " node index " + std::to_string(ni) + " out of range"; return false; }
			sk.NodeOfBone[b] = ni;
			sk.BoneOfNode[ni] = b;
			sk.ParentBone[b] = -1;
		}

		for (int b = 0; b < nb; b++)
		{
			const TSharedPtr<MmdJsonValue>& node = (*nodes)[sk.NodeOfBone[b]];
			TSharedPtr<MmdJsonValue> nm = JsonGet(node, "name");
			sk.Names[b] = nm.IsValid() ? ToUtf8(MiniJson::Str(nm)) : ("bone_" + std::to_string(b));
			const NodeFloats& F = nf[sk.NodeOfBone[b]];
			sk.RestPos[b] = ReadVec3(F.HasT, F.T);
			sk.RestRot[b] = ReadQuat(F.HasR, F.R);
			if (F.HasS)
			{
				const std::vector<float>& s = F.S;
				if (s.size() < 3) { err = "bone '" + sk.Names[b] + "' scale has fewer than 3 elements"; return false; }
				if (std::fabs(s[0] - 1.0f) > 1e-6f ||
				    std::fabs(s[1] - 1.0f) > 1e-6f ||
				    std::fabs(s[2] - 1.0f) > 1e-6f)
				{ err = "bone '" + sk.Names[b] + "' has non-unit scale"; return false; }
			}
			if (const FMmdJsonArray* children = MiniJson::Arr(JsonGet(node, "children")))
				for (int k = 0; k < children->Num(); k++)
				{
					const int cn = MiniJson::Int((*children)[k]);
					const int cb = (cn >= 0 && cn < static_cast<int>(sk.BoneOfNode.size())) ? sk.BoneOfNode[cn] : -1;
					if (cb >= 0) sk.ParentBone[cb] = b;
				}
		}
		return true;
	}

	// 移植元 GltfSkeleton.cs:95-109 (TopologicalOrder)。
	// List.Sort は不安定だが、比較子が (depth, index) の全順序なので結果は一意 = std::sort と同じ。
	std::vector<int> Skeleton::TopologicalOrder() const
	{
		const int nb = BoneCount;
		std::vector<int> depth(nb);
		for (int b = 0; b < nb; b++)
		{
			int d = 0, p = ParentBone[b];
			while (p >= 0) { d++; p = ParentBone[p]; if (d > nb) return std::vector<int>(); }
			depth[b] = d;
		}
		std::vector<int> idx(nb);
		for (int b = 0; b < nb; b++) idx[b] = b;
		std::sort(idx.begin(), idx.end(), [&](int x, int y) { return depth[x] != depth[y] ? depth[x] < depth[y] : x < y; });
		return idx;
	}

	// 移植元 GltfAnim.cs:27-37 (Find)
	int AnimSampler::Find(float t) const
	{
		int lo = 0, hi = static_cast<int>(Times.size()) - 1;
		if (t <= Times[0]) return 0;
		if (t >= Times[hi]) return hi;
		while (hi - lo > 1)
		{
			const int mid = (lo + hi) / 2;
			if (Times[mid] <= t) lo = mid; else hi = mid;
		}
		return lo;
	}

	// 移植元 GltfAnim.cs:67-74 (GetSpan)。Math.Min/Max(float) は NaN を伝播する。
	void AnimSampler::GetSpan(float t, int& i0, int& i1, float& u) const
	{
		i0 = Find(t);
		i1 = std::min(i0 + 1, static_cast<int>(Times.size()) - 1);
		if (Interpolation == "STEP" || i0 == i1) { u = 0.0f; return; }
		const float span = Times[i1] - Times[i0];
		if (span > 0.0f)
		{
			const float x = (t - Times[i0]) / span;
			const float mx = std::isnan(x) ? x : (x > 0.0f ? x : 0.0f);   // Math.Max(0f, x)
			u = std::isnan(mx) ? mx : (mx < 1.0f ? mx : 1.0f);            // Math.Min(1f, ·)
		}
		else u = 0.0f;
	}

	// 移植元 GltfAnim.cs:39-46 (SampleVec3)
	Vec3 AnimSampler::SampleVec3(float t) const
	{
		int i0, i1; float u;
		GetSpan(t, i0, i1, u);
		const Vec3 a(Values[i0 * 3], Values[i0 * 3 + 1], Values[i0 * 3 + 2]);
		if (i0 == i1 || u <= 0.0f) return a;
		const Vec3 b(Values[i1 * 3], Values[i1 * 3 + 1], Values[i1 * 3 + 2]);
		return a + (b - a) * u;
	}

	// 移植元 GltfAnim.cs:48-56 (SampleQuat)
	Quat AnimSampler::SampleQuat(float t) const
	{
		int i0, i1; float u;
		GetSpan(t, i0, i1, u);
		const Quat a(Values[i0 * 4], Values[i0 * 4 + 1], Values[i0 * 4 + 2], Values[i0 * 4 + 3]);
		if (i0 == i1 || u <= 0.0f) return a.Normalized();
		const Quat b(Values[i1 * 4], Values[i1 * 4 + 1], Values[i1 * 4 + 2], Values[i1 * 4 + 3]);
		return Quat::Slerp(a.Normalized(), b.Normalized(), u);
	}

	// 移植元 GltfAnim.cs:140-143 (SampleLocal)
	void Animation::SampleLocal(int node, float t, const Vec3& restPos, const Quat& restRot, Vec3& outPos, Quat& outRot) const
	{
		auto ts = Translation.find(node);
		outPos = ts != Translation.end() ? ts->second->SampleVec3(t) : restPos;
		auto rs = Rotation.find(node);
		outRot = rs != Rotation.end() ? rs->second->SampleQuat(t) : restRot;
	}

	// 移植元 GltfAnim.cs:88-134 (Load)
	bool LoadAnimation(const GlbFile& glb, int animIndex, Animation& result, std::string& err)
	{
		const FMmdJsonArray* anims = GlbArr(glb, "animations");
		if (!anims || anims->Num() == 0) { err = "GLB has no animations"; return false; }
		if (animIndex < 0 || animIndex >= anims->Num())
		{ err = "animation " + std::to_string(animIndex) + " out of range"; return false; }

		const TSharedPtr<MmdJsonValue>& a = (*anims)[animIndex];
		result = Animation();
		{
			TSharedPtr<MmdJsonValue> nm = JsonGet(a, "name");
			if (nm.IsValid()) result.Name = ToUtf8(MiniJson::Str(nm));
		}

		const FMmdJsonArray* samplersJson = MiniJson::Arr(JsonGet(a, "samplers"));
		const FMmdJsonArray* channels = MiniJson::Arr(JsonGet(a, "channels"));
		std::map<int, std::shared_ptr<AnimSampler>> cache;
		std::set<float> timeSet;   // SortedSet<float>

		auto GetSampler = [&](int si, std::shared_ptr<AnimSampler>& out) -> bool
		{
			auto it = cache.find(si);
			if (it != cache.end()) { out = it->second; return true; }
			if (!samplersJson || si < 0 || si >= samplersJson->Num()) { err = "sampler index out of range"; return false; }
			const TSharedPtr<MmdJsonValue>& sj = (*samplersJson)[si];
			std::string interp = "LINEAR";
			{
				TSharedPtr<MmdJsonValue> iv = JsonGet(sj, "interpolation");
				if (iv.IsValid()) interp = ToUtf8(MiniJson::Str(iv));
			}
			if (interp == "CUBICSPLINE") { err = "CUBICSPLINE interpolation unsupported"; return false; }
			auto s = std::make_shared<AnimSampler>();
			int tc = 0, vc = 0;
			if (!ReadFloatAccessor(glb, JsonInt(sj, "input", -1), s->Times, tc, err)) return false;
			if (tc != 1) { err = "sampler input is not SCALAR"; return false; }
			if (!ReadFloatAccessor(glb, JsonInt(sj, "output", -1), s->Values, vc, err)) return false;
			s->Components = vc;
			s->Interpolation = interp;
			cache[si] = s;
			for (float t : s->Times) timeSet.insert(t);
			out = s;
			return true;
		};

		if (channels)
			for (int k = 0; k < channels->Num(); k++)
			{
				const TSharedPtr<MmdJsonValue>& ch = (*channels)[k];
				TSharedPtr<MmdJsonValue> target = JsonGet(ch, "target");
				if (!MiniJson::Obj(target)) continue;
				TSharedPtr<MmdJsonValue> nodeNode = JsonGet(target, "node");
				if (!nodeNode.IsValid()) continue;   // ノード無し = 無効チャンネル
				const int node = MiniJson::Int(nodeNode);
				std::string path;
				{
					TSharedPtr<MmdJsonValue> pv = JsonGet(target, "path");
					if (pv.IsValid()) path = ToUtf8(MiniJson::Str(pv));
				}
				std::shared_ptr<AnimSampler> sampler;
				if (!GetSampler(JsonInt(ch, "sampler", -1), sampler)) return false;
				if (path == "rotation") result.Rotation[node] = sampler;
				else if (path == "translation") result.Translation[node] = sampler;
				else if (path == "scale") result.Scale[node] = sampler;
			}

		result.Times.assign(timeSet.begin(), timeSet.end());
		if (result.Times.empty()) { err = "animation has no keyframes"; return false; }
		for (auto& kv : result.Rotation) if (kv.second->Times.empty() || kv.second->Values.size() < kv.second->Times.size() * 4) { err = "rotation sampler is malformed"; return false; }
		for (auto& kv : result.Translation) if (kv.second->Times.empty() || kv.second->Values.size() < kv.second->Times.size() * 3) { err = "translation sampler is malformed"; return false; }
		return true;
	}

	// 移植元 MmdConvert.cs:43-44 (GltfToMmdPos float 版) / :47 (GltfToMmdRot) / :49 (GltfToMmd)
	RigidTransform GltfToMmd(const Vec3& pos, const Quat& rot, float unitScale)
	{
		const Vec3 p(pos.x / unitScale, pos.y / unitScale, -pos.z / unitScale);
		const Quat r(-rot.x, -rot.y, rot.z, rot.w);
		return RigidTransform(r, p);
	}

	// 移植元 PhysBake/Program.cs:~141-160 (FK ループ)
	std::vector<std::vector<RigidTransform>> ComputeFk(const Skeleton& skel, const Animation& anim, float unitScale)
	{
		const int nb = skel.BoneCount;
		const std::vector<int> order = skel.TopologicalOrder();
		std::vector<std::vector<RigidTransform>> world;
		if (static_cast<int>(order.size()) != nb) return world;
		const int frames = static_cast<int>(anim.Times.size());
		world.resize(frames);
		for (int f = 0; f < frames; f++)
		{
			std::vector<RigidTransform>& row = world[f];
			row.assign(nb, RigidTransform());
			const float t = anim.Times[f];
			for (int b : order)
			{
				Vec3 lpos; Quat lrot;
				anim.SampleLocal(skel.NodeOfBone[b], t, skel.RestPos[b], skel.RestRot[b], lpos, lrot);
				const RigidTransform local = GltfToMmd(lpos, lrot, unitScale);
				const int p = skel.ParentBone[b];
				row[b] = p >= 0 ? row[p] * local : local;
			}
		}
		return world;
	}

	// 移植元 PhysBake/Program.cs:~110-135 (バインド姿勢の検算)
	float CheckBindPose(const Skeleton& skel, const PmxPhysicsModel& model, float unitScale)
	{
		const int nb = skel.BoneCount;
		const std::vector<int> order = skel.TopologicalOrder();
		if (static_cast<int>(order.size()) != nb || model.BonePositions.Num() < nb) return INFINITY;
		std::vector<RigidTransform> restWorld(nb);
		float worst = 0.0f;
		for (int b : order)
		{
			const RigidTransform local = GltfToMmd(skel.RestPos[b], skel.RestRot[b], unitScale);
			const int p = skel.ParentBone[b];
			restWorld[b] = p >= 0 ? restWorld[p] * local : local;
			const float e = (restWorld[b].Origin - model.BonePositions[b]).Length();
			if (e > worst) worst = e;
		}
		return worst;
	}
}
