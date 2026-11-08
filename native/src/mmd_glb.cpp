#include "mmd_glb.h"

#include <cstdlib>
#include <cstring>

namespace mmdnative
{
	using namespace MmdPhysics;

	std::string ToUtf8(const FString& s)
	{
		return MmdShim::Utf16ToUtf8(*s, static_cast<size_t>(s.Len()));
	}

	TSharedPtr<MmdJsonValue> JsonGet(const TSharedPtr<MmdJsonValue>& node, const char* key)
	{
		return MiniJson::Get(MiniJson::Obj(node), FString(FUTF8ToTCHAR(key)));
	}

	// 移植元 Glb.cs:84-87 (Int)。
	int JsonInt(const TSharedPtr<MmdJsonValue>& node, const char* key, int fallback)
	{
		TSharedPtr<MmdJsonValue> v = JsonGet(node, key);
		return v.IsValid() ? MiniJson::Int(v) : fallback;
	}

	static uint32_t ReadU32(const std::vector<uint8_t>& d, size_t o)
	{
		uint32_t v;
		std::memcpy(&v, d.data() + o, 4);   // little endian 前提 (BitConverter.ToUInt32 と同じ)
		return v;
	}

	// 移植元 Glb.cs:42-72
	bool LoadGlb(const std::vector<uint8_t>& d, GlbFile& out, std::string& err)
	{
		constexpr uint32_t MagicGlb = 0x46546C67, ChunkJson = 0x4E4F534A, ChunkBin = 0x004E4942;
		if (d.size() < 12 || ReadU32(d, 0) != MagicGlb) { err = "GLB magic invalid (not glTF binary)"; return false; }
		int off = 12;
		bool hasJson = false;
		FString json;
		out.Bin.clear();
		out.JsonText.clear();
		while (static_cast<size_t>(off) + 8 <= d.size())
		{
			const int clen = static_cast<int>(ReadU32(d, off));
			const uint32_t ctype = ReadU32(d, off + 4);
			const int cdata = off + 8;
			if (clen < 0 || static_cast<size_t>(cdata) + static_cast<size_t>(clen) > d.size())
			{ err = "GLB chunk length exceeds file length"; return false; }
			if (ctype == ChunkJson)
			{
				out.JsonText.assign(reinterpret_cast<const char*>(d.data() + cdata), static_cast<size_t>(clen));
				json = FString(FUTF8ToTCHAR(reinterpret_cast<const char*>(d.data() + cdata), clen));
				hasJson = true;
			}
			else if (ctype == ChunkBin)
			{
				out.Bin.assign(d.begin() + cdata, d.begin() + cdata + clen);
			}
			off = cdata + clen;
			if ((clen & 3) != 0) off += 4 - (clen & 3);
		}
		if (!hasJson) { err = "GLB has no JSON chunk"; return false; }
		out.Json = MiniJson::Parse(json);
		if (MiniJson::Obj(out.Json) == nullptr) { err = "GLB JSON chunk is not an object"; return false; }
		return true;
	}

	// 移植元 Glb.cs:78-82
	const FMmdJsonArray* GlbArr(const GlbFile& g, const char* key)
	{
		return MiniJson::Arr(JsonGet(g.Json, key));
	}

	// 移植元 Glb.cs:101-142
	bool ReadFloatAccessor(const GlbFile& g, int accessorIndex, std::vector<float>& result, int& components,
	                       std::string& err)
	{
		const FMmdJsonArray* accessors = GlbArr(g, "accessors");
		if (!accessors) { err = "glTF has no 'accessors'"; return false; }
		if (accessorIndex < 0 || accessorIndex >= accessors->Num())
		{ err = "accessor " + std::to_string(accessorIndex) + " out of range"; return false; }
		const TSharedPtr<MmdJsonValue>& acc = (*accessors)[accessorIndex];

		const int compType = JsonInt(acc, "componentType", -1);
		if (compType != 5126)
		{ err = "accessor " + std::to_string(accessorIndex) + ": componentType " + std::to_string(compType) + " unsupported (FLOAT only)"; return false; }
		if (JsonGet(acc, "sparse").IsValid())
		{ err = "accessor " + std::to_string(accessorIndex) + ": sparse unsupported"; return false; }

		std::string type = "SCALAR";
		{
			TSharedPtr<MmdJsonValue> t = JsonGet(acc, "type");
			if (t.IsValid()) type = ToUtf8(MiniJson::Str(t));
		}
		if (type == "SCALAR") components = 1;
		else if (type == "VEC2") components = 2;
		else if (type == "VEC3") components = 3;
		else if (type == "VEC4") components = 4;
		else if (type == "MAT2") components = 4;
		else if (type == "MAT3") components = 9;
		else if (type == "MAT4") components = 16;
		else { err = "accessor " + std::to_string(accessorIndex) + ": unknown type '" + type + "'"; return false; }

		const int count = JsonInt(acc, "count", 0);
		const int accOffset = JsonInt(acc, "byteOffset", 0);
		const int bvIndex = JsonInt(acc, "bufferView", -1);
		result.assign(static_cast<size_t>(count) * static_cast<size_t>(components), 0.0f);
		if (bvIndex < 0) return true;   // bufferView 省略 = 全ゼロ

		const FMmdJsonArray* views = GlbArr(g, "bufferViews");
		if (!views || bvIndex >= views->Num()) { err = "bufferView out of range"; return false; }
		const TSharedPtr<MmdJsonValue>& bv = (*views)[bvIndex];
		const int bvOffset = JsonInt(bv, "byteOffset", 0);
		int stride = JsonInt(bv, "byteStride", 0);
		if (stride <= 0) stride = components * 4;
		const int bufIndex = JsonInt(bv, "buffer", 0);
		if (bufIndex != 0) { err = "buffer other than 0 unsupported"; return false; }

		const int baseOff = bvOffset + accOffset;
		for (int i = 0; i < count; i++)
		{
			const long long o = static_cast<long long>(baseOff) + static_cast<long long>(i) * stride;
			if (o < 0 || o + components * 4 > static_cast<long long>(g.Bin.size()))
			{ err = "accessor reads past the BIN chunk"; return false; }
			for (int c = 0; c < components; c++)
				std::memcpy(&result[static_cast<size_t>(i) * components + c], g.Bin.data() + o + c * 4, 4);
		}
		return true;
	}

	// ---- ReadNodeFloats: 必要最小限の JSON 走査 (構造は MiniJson と同じ文法) ----
	namespace
	{
		struct Scan
		{
			const std::string& s;
			size_t i = 0;
			bool ok = true;
			explicit Scan(const std::string& t) : s(t) {}
			void Ws() { while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++; }
			bool Lit(char c) { Ws(); if (i < s.size() && s[i] == c) { i++; return true; } return false; }
			// 文字列 (エスケープは \X を 2 文字として飛ばす。キー照合は ASCII のキーのみ想定)。
			std::string Str()
			{
				std::string r;
				Ws();
				if (i >= s.size() || s[i] != '"') { ok = false; return r; }
				i++;
				while (i < s.size() && s[i] != '"')
				{
					if (s[i] == '\\' && i + 1 < s.size()) { r += s[i]; r += s[i + 1]; i += 2; }
					else r += s[i++];
				}
				if (i >= s.size()) ok = false; else i++;
				return r;
			}
			void Skip()
			{
				Ws();
				if (i >= s.size()) { ok = false; return; }
				const char c = s[i];
				if (c == '"') { Str(); return; }
				if (c == '{' || c == '[')
				{
					const char close = c == '{' ? '}' : ']';
					i++;
					Ws();
					if (i < s.size() && s[i] == close) { i++; return; }
					for (;;)
					{
						if (c == '{') { Str(); if (!Lit(':')) { ok = false; return; } }
						Skip();
						if (!ok) return;
						Ws();
						if (i < s.size() && s[i] == ',') { i++; continue; }
						if (i < s.size() && s[i] == close) { i++; return; }
						ok = false; return;
					}
				}
				while (i < s.size() && s[i] != ',' && s[i] != '}' && s[i] != ']' && s[i] != ' ' && s[i] != '\n' && s[i] != '\r' && s[i] != '\t') i++;
			}
			// 数値配列 -> float (テキスト直接丸め)。数値以外が混じれば ok=false。
			void FloatArray(std::vector<float>& v)
			{
				v.clear();
				if (!Lit('[')) { ok = false; return; }
				Ws();
				if (i < s.size() && s[i] == ']') { i++; return; }
				for (;;)
				{
					Ws();
					const size_t st = i;
					while (i < s.size() && (s[i] == '-' || s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E' || (s[i] >= '0' && s[i] <= '9'))) i++;
					if (st == i) { ok = false; return; }
					v.push_back(std::strtof(s.substr(st, i - st).c_str(), nullptr));
					Ws();
					if (i < s.size() && s[i] == ',') { i++; continue; }
					if (i < s.size() && s[i] == ']') { i++; return; }
					ok = false; return;
				}
			}
		};
	}

	bool ReadNodeFloats(const GlbFile& g, std::vector<NodeFloats>& out, std::string& err)
	{
		out.clear();
		Scan sc(g.JsonText);
		if (!sc.Lit('{')) { err = "JSON root is not an object"; return false; }
		bool found = false;
		sc.Ws();
		if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == '}') { err = "no nodes"; return false; }
		for (;;)
		{
			const std::string key = sc.Str();
			if (!sc.Lit(':')) { err = "JSON parse error"; return false; }
			if (key == "nodes")
			{
				found = true;
				out.clear();
				if (!sc.Lit('[')) { err = "nodes is not an array"; return false; }
				sc.Ws();
				if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == ']') sc.i++;
				else for (;;)
				{
					NodeFloats nf;
					if (!sc.Lit('{')) { err = "node is not an object"; return false; }
					sc.Ws();
					if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == '}') sc.i++;
					else for (;;)
					{
						const std::string k = sc.Str();
						if (!sc.Lit(':')) { err = "JSON parse error"; return false; }
						sc.Ws();
						const bool isArr = sc.i < g.JsonText.size() && g.JsonText[sc.i] == '[';
						if (k == "translation" && isArr) { sc.FloatArray(nf.T); nf.HasT = true; }
						else if (k == "rotation" && isArr) { sc.FloatArray(nf.R); nf.HasR = true; }
						else if (k == "scale" && isArr) { sc.FloatArray(nf.S); nf.HasS = true; }
						else sc.Skip();
						if (!sc.ok) { err = "JSON parse error in nodes"; return false; }
						sc.Ws();
						if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == ',') { sc.i++; continue; }
						if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == '}') { sc.i++; break; }
						err = "JSON parse error in node"; return false;
					}
					out.push_back(std::move(nf));
					sc.Ws();
					if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == ',') { sc.i++; continue; }
					if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == ']') { sc.i++; break; }
					err = "JSON parse error in nodes array"; return false;
				}
			}
			else sc.Skip();
			if (!sc.ok) { err = "JSON parse error"; return false; }
			sc.Ws();
			if (sc.i < g.JsonText.size() && g.JsonText[sc.i] == ',') { sc.i++; continue; }
			break;
		}
		if (!found) { err = "no nodes"; return false; }
		return true;
	}
}
