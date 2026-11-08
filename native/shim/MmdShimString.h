// FString / FCString / FUTF8ToTCHAR の UE5 互換 (TCHAR = char16_t)。
#pragma once

#include "MmdShimCore.h"
#include <string>

enum class ESearchCase { CaseSensitive, IgnoreCase };

namespace MmdShim
{
	// UE の大小無視比較は ASCII 以外では TChar テーブルを使うが、ここでは ASCII の A-Z のみ畳む。
	inline TCHAR FoldChar(TCHAR c) { return (c >= u'A' && c <= u'Z') ? static_cast<TCHAR>(c + (u'a' - u'A')) : c; }
	std::u16string Utf8ToUtf16(const char* s, size_t len);
	std::string Utf16ToUtf8(const TCHAR* s, size_t len);
	std::u16string FormatPrintf(const TCHAR* Fmt, ...);
}

class FString
{
public:
	FString() = default;
	FString(const TCHAR* S) : Str(S ? S : u"") {}
	FString(const std::u16string& S) : Str(S) {}
	FString(std::u16string&& S) : Str(std::move(S)) {}

	const TCHAR* operator*() const { return Str.c_str(); }
	int32 Len() const { return static_cast<int32>(Str.size()); }
	bool IsEmpty() const { return Str.empty(); }
	const std::u16string& Raw() const { return Str; }

	FString Mid(int32 Start, int32 Count = 0x7fffffff) const
	{
		if (Start < 0) { Count += Start; Start = 0; }
		if (Count < 0 || Start >= Len()) return FString();
		return FString(Str.substr(static_cast<size_t>(Start), static_cast<size_t>(Count)));
	}
	void AppendChar(TCHAR C) { Str.push_back(C); }
	FString& operator+=(const FString& O) { Str += O.Str; return *this; }
	FString& operator+=(const TCHAR* O) { Str += O; return *this; }
	FString& operator+=(TCHAR C) { Str.push_back(C); return *this; }
	friend FString operator+(const FString& A, const FString& B) { return FString(A.Str + B.Str); }
	friend FString operator+(const FString& A, const TCHAR* B) { return FString(A.Str + B); }
	friend FString operator+(const TCHAR* A, const FString& B) { return FString(std::u16string(A) + B.Str); }

	const TCHAR& operator[](int32 I) const { return Str[static_cast<size_t>(I)]; }

	// UE の既定は大小無視 (== / Equals / Contains / Find すべて)。
	bool Equals(const FString& O, ESearchCase C = ESearchCase::IgnoreCase) const
	{
		if (C == ESearchCase::CaseSensitive) return Str == O.Str;
		return Compare(O) == 0;
	}
	int32 Find(const FString& Sub, ESearchCase C = ESearchCase::IgnoreCase) const
	{
		if (Sub.Str.empty()) return 0;
		for (size_t i = 0; i + Sub.Str.size() <= Str.size(); i++)
			if (SameAt(i, Sub, C)) return static_cast<int32>(i);
		return INDEX_NONE;
	}
	bool Contains(const FString& Sub, ESearchCase C = ESearchCase::IgnoreCase) const { return Find(Sub, C) != INDEX_NONE; }

	static FString Printf(const TCHAR* Fmt, ...);

	friend bool operator==(const FString& A, const FString& B) { return A.Compare(B) == 0; }
	friend bool operator!=(const FString& A, const FString& B) { return A.Compare(B) != 0; }
	friend bool operator==(const FString& A, const TCHAR* B) { return A.Compare(FString(B)) == 0; }
	friend bool operator!=(const FString& A, const TCHAR* B) { return A.Compare(FString(B)) != 0; }
	friend bool operator==(const TCHAR* A, const FString& B) { return B.Compare(FString(A)) == 0; }
	friend bool operator!=(const TCHAR* A, const FString& B) { return B.Compare(FString(A)) != 0; }
	// TMap のキー順序用 (== と整合する大小無視の順序)。
	friend bool operator<(const FString& A, const FString& B) { return A.Compare(B) < 0; }

private:
	int32 Compare(const FString& O) const
	{
		const size_t n = Str.size() < O.Str.size() ? Str.size() : O.Str.size();
		for (size_t i = 0; i < n; i++)
		{
			const TCHAR a = MmdShim::FoldChar(Str[i]), b = MmdShim::FoldChar(O.Str[i]);
			if (a != b) return a < b ? -1 : 1;
		}
		return Str.size() == O.Str.size() ? 0 : (Str.size() < O.Str.size() ? -1 : 1);
	}
	bool SameAt(size_t Pos, const FString& Sub, ESearchCase C) const
	{
		for (size_t j = 0; j < Sub.Str.size(); j++)
		{
			TCHAR a = Str[Pos + j], b = Sub.Str[j];
			if (C == ESearchCase::IgnoreCase) { a = MmdShim::FoldChar(a); b = MmdShim::FoldChar(b); }
			if (a != b) return false;
		}
		return true;
	}
	std::u16string Str;
};

struct FCString
{
	// ロケール非依存 (常に "C" ロケール)。
	static double Atod(const TCHAR* S);
	static int32 Strtoi(const TCHAR* S, TCHAR** End, int32 Base);
};

// UTF-8 -> TCHAR (UTF-16)。FString(FUTF8ToTCHAR(p, len)) と書けるよう FString へ暗黙変換する。
class FUTF8ToTCHAR
{
public:
	FUTF8ToTCHAR(const ANSICHAR* S, int32 Len) : Str(MmdShim::Utf8ToUtf16(S, static_cast<size_t>(Len))) {}
	explicit FUTF8ToTCHAR(const ANSICHAR* S) : Str(MmdShim::Utf8ToUtf16(S, std::strlen(S))) {}
	const TCHAR* Get() const { return Str.c_str(); }
	int32 Length() const { return static_cast<int32>(Str.size()); }
	operator FString() const { return FString(Str); }
private:
	std::u16string Str;
};
