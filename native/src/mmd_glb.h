// GLB (glTF binary) コンテナと accessor 読み出し。
// 移植元: mmd2gltf-cs src/Mmd2Gltf.Core/Gltf/Glb.cs (読み出し部分のみ。書き込み・追記は対象外)。
// JSON は vendor の MmdMiniJson (MmdPhysics::MiniJson) で読む。
#pragma once
#include "MmdMiniJson.h"

#include <cstdint>
#include <string>
#include <vector>

namespace mmdnative
{
	struct GlbFile
	{
		// vendor MiniJson::Parse の戻り値型 (TSharedPtr<MmdJsonValue>)。ルートは Object。
		TSharedPtr<MmdPhysics::MmdJsonValue> Json;
		std::vector<uint8_t> Bin;
		// JSON チャンクの生バイト列 (UTF-8)。ReadNodeFloats が使う。
		std::string JsonText;
	};

	// nodes[i] の translation / rotation / scale を、JSON の数値テキストから**直接 float に丸めて**読む。
	// 移植元は System.Text.Json の GetValue<float>() = テキスト -> float の 1 回丸め。vendor MiniJson は
	// テキスト -> double -> float の 2 回丸めで、float 境界の中点上にある値 (実 GLB に多数ある) で
	// 1 ULP ずれるため、骨格の rest 値だけはこの経路で読む (ずれると FK がビット一致しない)。
	struct NodeFloats
	{
		bool HasT = false, HasR = false, HasS = false;
		std::vector<float> T, R, S;
	};
	bool ReadNodeFloats(const GlbFile& g, std::vector<NodeFloats>& out, std::string& err);

	// 移植元 Glb.cs:42-72 (Parse)。例外の代わりに false + err。
	bool LoadGlb(const std::vector<uint8_t>& bytes, GlbFile& out, std::string& err);

	// 移植元 Glb.cs:78-82 (Arr)。無ければ nullptr。
	const MmdPhysics::FMmdJsonArray* GlbArr(const GlbFile& g, const char* key);

	// 移植元 Glb.cs:101-142 (ReadFloatAccessor)。FLOAT のみ / 非 sparse。
	bool ReadFloatAccessor(const GlbFile& g, int accessorIndex, std::vector<float>& out, int& components,
	                       std::string& err);

	// 共通ヘルパ: JsonObject の項目を取る。存在しなければ fallback。
	int JsonInt(const TSharedPtr<MmdPhysics::MmdJsonValue>& node, const char* key, int fallback);
	TSharedPtr<MmdPhysics::MmdJsonValue> JsonGet(const TSharedPtr<MmdPhysics::MmdJsonValue>& node, const char* key);
	std::string ToUtf8(const FString& s);
}
