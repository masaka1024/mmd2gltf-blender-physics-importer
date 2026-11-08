// glTF スケルトン / アニメーション / FK。
// 移植元: mmd2gltf-cs GltfSkeleton.cs, GltfAnim.cs, MmdConvert.cs, PhysBake/Program.cs。
#pragma once
#include "MmdPmxPhysicsData.h"
#include "MmdRigidTransform.h"
#include "mmd_glb.h"

#include <map>
#include <memory>
#include <string>
#include <vector>

namespace mmdnative
{
	using MmdPhysics::Quat;
	using MmdPhysics::RigidTransform;
	using MmdPhysics::Vec3;

	struct Skeleton
	{
		int BoneCount = 0;
		std::vector<int> NodeOfBone, BoneOfNode, ParentBone;
		std::vector<Vec3> RestPos;
		std::vector<Quat> RestRot;
		std::vector<std::string> Names;

		// 親→子の順 (FK 合成順)。循環があれば空を返す。
		std::vector<int> TopologicalOrder() const;
	};
	bool LoadSkeleton(const GlbFile& glb, Skeleton& out, std::string& err);

	struct AnimSampler
	{
		std::vector<float> Times;
		std::vector<float> Values;
		int Components = 0;
		std::string Interpolation = "LINEAR";

		Vec3 SampleVec3(float t) const;
		Quat SampleQuat(float t) const;
	private:
		int Find(float t) const;
		void GetSpan(float t, int& i0, int& i1, float& u) const;
	};

	struct Animation
	{
		std::string Name;
		std::vector<float> Times;   // 全 input の和集合 (昇順)
		std::map<int, std::shared_ptr<AnimSampler>> Rotation, Translation, Scale;

		void SampleLocal(int node, float t, const Vec3& restPos, const Quat& restRot, Vec3& outPos, Quat& outRot) const;
	};
	bool LoadAnimation(const GlbFile& glb, int index, Animation& out, std::string& err);

	RigidTransform GltfToMmd(const Vec3& pos, const Quat& rot, float unitScale);

	// [frame][bone] = raw PMX 空間のボーン world。階層が循環していれば空。
	std::vector<std::vector<RigidTransform>> ComputeFk(const Skeleton&, const Animation&, float unitScale);

	// バインド姿勢を FK 合成した world と model.BonePositions の最大ずれ。
	float CheckBindPose(const Skeleton&, const MmdPhysics::PmxPhysicsModel&, float unitScale);
}
