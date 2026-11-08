// 物理ベイク (FK のボーン world 列 -> 物理ボーンの world 列)。
// 移植元: mmd2gltf-cs PhysicsBaker.cs (Run, IsFinite) と PhysBake/Program.cs の上書き処理。
#pragma once
#include "MmdPmxPhysicsBuilder.h"
#include "mmd_fk.h"

#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace mmdnative
{
	using MmdPhysics::PhysicsWorld;
	using MmdPhysics::PmxPhysicsModel;

	// physbake --playback と同じ設定 (Program.cs:120-134)。
	struct PlaybackSettings
	{
		float FixedTimeStep = 1.0f / 60.0f;
		int SubSteps = 2;
		int SolverIterations = 10;
		int JointVelocityIterations = 40;
		float JointMaxCorrectionVel = 30.0f;
		bool UseSplitImpulse = true;
		bool UseJointSplitImpulse = true;
		bool BoneMergeMode = true;
		bool AlignBonePositions = false;
		float AlignRotClampAlpha = 0.5f;
		float WarmupSeconds = 2.0f;
	};

	// 移植元 PhysicsBaker.cs:80-89 (ワールドへの設定書き込み。重力はエンジン既定のまま)。
	void ApplySolverSettings(PhysicsWorld&, const PlaybackSettings&);

	struct BakeResult
	{
		std::vector<int> PhysicsBones;                       // 昇順
		std::vector<std::vector<RigidTransform>> BoneWorld;  // [frame][bone] 全ボーン。物理ボーンを上書き済み
		std::string NanBone;                                 // 空 = 発散なし
	};

	// fk が空 (ComputeFk が循環で失敗) / 行数が times と合わない場合は空の結果 + NanBone 空 を返す
	// (呼び出し側は PhysicsBones.empty() で判定する)。progress(frame, frames) は null 可。
	BakeResult RunBake(const std::shared_ptr<PmxPhysicsModel>& model,
	                   const std::vector<std::vector<RigidTransform>>& fk,
	                   const std::vector<float>& times,
	                   const PlaybackSettings& opt,
	                   const std::function<void(int, int)>& progress);
}
