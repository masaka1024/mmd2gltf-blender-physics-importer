#include "mmd_bake.h"

#include <cmath>
#include <set>
#include "mmd_glb.h"

namespace mmdnative
{
	using namespace MmdPhysics;

	namespace
	{
		// 移植元 PhysicsBaker.cs:183-190 (IsFinite)
		bool IsFinite(const RigidTransform& t)
		{
			const Vec3& v = t.Origin; const Quat& q = t.Rotation;
			return !(std::isnan(v.x) || std::isnan(v.y) || std::isnan(v.z) ||
			         std::isinf(v.x) || std::isinf(v.y) || std::isinf(v.z) ||
			         std::isnan(q.x) || std::isnan(q.y) || std::isnan(q.z) || std::isnan(q.w) ||
			         std::isinf(q.x) || std::isinf(q.y) || std::isinf(q.z) || std::isinf(q.w));
		}
	}

	// 移植元 PhysicsBaker.cs:82-88 (>0 のときだけ書く。SimHz=60 -> FixedTimeStep = 1f/60f)
	void ApplySolverSettings(PhysicsWorld& world, const PlaybackSettings& opt)
	{
		if (opt.FixedTimeStep > 0.0f) world.FixedTimeStep = opt.FixedTimeStep;
		if (opt.SubSteps > 0) world.SubSteps = opt.SubSteps;
		if (opt.SolverIterations > 0) world.SolverIterations = opt.SolverIterations;
		if (opt.JointVelocityIterations > 0) world.JointVelocityIterations = opt.JointVelocityIterations;
		if (opt.JointMaxCorrectionVel > 0.0f) world.JointMaxCorrectionVel = opt.JointMaxCorrectionVel;
		world.UseSplitImpulse = opt.UseSplitImpulse;
		world.UseJointSplitImpulse = opt.UseJointSplitImpulse;
	}

	bool IsFiniteTransform(const RigidTransform& t) { return IsFinite(t); }

	std::vector<int> CollectPhysicsBones(const PmxPhysicsBuilder& builder, int nb)
	{
		std::set<int> s;
		for (const BoneLink& link : builder.BoneLinks)
			if (link.Mode != EPhysicsMode::BoneFollow && link.BoneIndex >= 0 && link.BoneIndex < nb)
				s.insert(link.BoneIndex);
		return std::vector<int>(s.begin(), s.end());
	}

	RigidTransform ResolvePhysicsBoneWorld(const BoneLink& link, const TArray<TOptional<RigidTransform>>& aligned,
	                                       bool needAligned, const PlaybackSettings& opt)
	{
		const int bi = link.BoneIndex;
		const bool useAligned = needAligned && aligned[bi].IsSet() &&
		                        (opt.AlignBonePositions ||
		                         (opt.BoneMergeMode && link.Mode == EPhysicsMode::DynamicBoneMerge));
		return useAligned
			? aligned[bi].GetValue()
			: link.Body->WorldTransform * link.BodyOffsetFromBone.Inverse();
	}

	// 移植元 PhysicsBaker.cs:71-181 (Run) + PhysBake/Program.cs:239-241 (物理ボーンの上書き)
	BakeResult RunBake(const std::shared_ptr<PmxPhysicsModel>& modelIn,
	                   const std::vector<std::vector<RigidTransform>>& fk,
	                   const std::vector<float>& times,
	                   const PlaybackSettings& opt,
	                   const std::function<void(int, int)>& progress)
	{
		BakeResult result;
		const int frames = static_cast<int>(times.size());
		if (!modelIn) { result.Error = "model is null"; return result; }
		if (fk.empty()) { result.Error = "FK is empty (cyclic bone hierarchy or no frames)"; return result; }
		if (static_cast<int>(fk.size()) != frames)
		{ result.Error = "FK frame count does not match times"; return result; }
		const TSharedPtr<PmxPhysicsModel> model(modelIn);
		const int nb = model->BoneNames.Num();
		for (const auto& row : fk)
			if (static_cast<int>(row.size()) != nb) { result.Error = "FK row size does not match bone count"; return result; }

		TSharedPtr<PmxPhysicsBuilder> builderPtr = PmxPhysicsBuilder::Build(model);
		if (!builderPtr.IsValid()) { result.Error = "PmxPhysicsBuilder::Build failed"; return result; }
		PmxPhysicsBuilder& builder = *builderPtr;
		PhysicsWorld& world = builder.World;
		ApplySolverSettings(world, opt);

		// 物理ボーン = BoneFollow でない剛体が紐づくボーン。
		result.PhysicsBones = CollectPhysicsBones(builder, nb);

		const bool needAligned = opt.AlignBonePositions ||
		                         (opt.BoneMergeMode && builder.HasBoneMergeBodies());

		// 全フレーム FK で埋めておき、物理ボーンだけ後で上書きする (Program.cs の fkWorldRaw 上書きと等価)。
		result.BoneWorld = fk;

		auto DrivenAt = [&](int frame)
		{
			return [&fk, frame, nb](int32 bi) -> TOptional<RigidTransform>
			{
				if (bi >= 0 && bi < nb) return TOptional<RigidTransform>(fk[frame][bi]);
				return TOptional<RigidTransform>();
			};
		};

		// 物理開始前に FK-rest で全剛体をボーン姿勢へ整合させる。
		{
			auto g0 = DrivenAt(0);
			builder.ApplyKinematicTargets(g0);
			builder.ResetBodiesToBonePoseFk(g0);
		}

		// ウォームアップ: float / float -> Math.Round(double) (偶数丸め)。
		const float warmQuot = opt.WarmupSeconds / world.FixedTimeStep;
		const int warmupSteps = static_cast<int>(std::nearbyint(static_cast<double>(warmQuot)));
		for (int i = 0; i < warmupSteps; i++) world.StepSimulation(world.FixedTimeStep);

		for (int f = 0; f < frames; f++)
		{
			if (f > 0)
			{
				auto g = DrivenAt(f);
				builder.ApplyKinematicTargets(g);
				float dt = times[f] - times[f - 1];
				if (dt <= 0.0f) dt = world.FixedTimeStep;
				world.StepSimulation(dt);
			}

			TArray<TOptional<RigidTransform>> aligned;
			if (needAligned)
			{
				auto g = DrivenAt(f);
				aligned = builder.ComputeAlignedBonePoses(g, opt.AlignRotClampAlpha, opt.AlignBonePositions);
			}

			std::vector<RigidTransform>& row = result.BoneWorld[f];
			for (const BoneLink& link : builder.BoneLinks)
			{
				if (link.Mode == EPhysicsMode::BoneFollow) continue;
				const int bi = link.BoneIndex;
				if (bi < 0 || bi >= nb) continue;

				RigidTransform boneWorld = ResolvePhysicsBoneWorld(link, aligned, needAligned, opt);

				// NaN ガード: 発散したらそのフレーム以降は直前の姿勢を保持する。
				if (!IsFinite(boneWorld))
				{
					if (!result.NanSeen)
					{
						result.NanSeen = true;
						const FString& nm = model->BoneNames[bi];
						result.NanBone = ToUtf8(nm);
						if (result.NanBone.empty()) result.NanBone = "#" + std::to_string(bi);
					}
					boneWorld = f > 0 ? result.BoneWorld[f - 1][bi] : RigidTransform::Identity;
				}
				row[bi] = boneWorld;
			}

			if (progress) progress(f, frames);
		}
		return result;
	}
}
