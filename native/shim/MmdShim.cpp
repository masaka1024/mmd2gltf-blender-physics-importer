#include "CoreMinimal.h"

#include <chrono>
#include <cstdarg>
#include <clocale>
#include <string>
#if PLATFORM_WINDOWS
	#include <locale.h>
#else
	#include <locale.h>
	#if defined(__APPLE__)
		#include <xlocale.h>
	#endif
#endif

namespace MmdShim
{
	std::u16string Utf8ToUtf16(const char* s, size_t len)
	{
		std::u16string out;
		out.reserve(len);
		size_t i = 0;
		while (i < len)
		{
			const unsigned char c = static_cast<unsigned char>(s[i]);
			uint32_t cp = 0xFFFD;
			size_t n = 1;
			if (c < 0x80) cp = c;
			else if (c >= 0xC2 && c <= 0xDF && i + 1 < len && (s[i + 1] & 0xC0) == 0x80)
			{ cp = ((c & 0x1Fu) << 6) | (s[i + 1] & 0x3Fu); n = 2; }
			else if (c >= 0xE0 && c <= 0xEF && i + 2 < len && (s[i + 1] & 0xC0) == 0x80 && (s[i + 2] & 0xC0) == 0x80)
			{
				const uint32_t v = ((c & 0x0Fu) << 12) | ((s[i + 1] & 0x3Fu) << 6) | (s[i + 2] & 0x3Fu);
				if (v >= 0x800 && !(v >= 0xD800 && v <= 0xDFFF)) { cp = v; n = 3; }
			}
			else if (c >= 0xF0 && c <= 0xF4 && i + 3 < len && (s[i + 1] & 0xC0) == 0x80 && (s[i + 2] & 0xC0) == 0x80 && (s[i + 3] & 0xC0) == 0x80)
			{
				const uint32_t v = ((c & 0x07u) << 18) | ((s[i + 1] & 0x3Fu) << 12) | ((s[i + 2] & 0x3Fu) << 6) | (s[i + 3] & 0x3Fu);
				if (v >= 0x10000 && v <= 0x10FFFF) { cp = v; n = 4; }
			}
			i += n;
			if (cp >= 0x10000) { cp -= 0x10000; out.push_back(static_cast<char16_t>(0xD800 + (cp >> 10))); out.push_back(static_cast<char16_t>(0xDC00 + (cp & 0x3FF))); }
			else out.push_back(static_cast<char16_t>(cp));
		}
		return out;
	}

	std::string Utf16ToUtf8(const TCHAR* s, size_t len)
	{
		std::string out;
		for (size_t i = 0; i < len; i++)
		{
			uint32_t cp = s[i];
			if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < len && s[i + 1] >= 0xDC00 && s[i + 1] <= 0xDFFF)
			{ cp = 0x10000 + ((cp - 0xD800) << 10) + (s[i + 1] - 0xDC00); i++; }
			else if (cp >= 0xD800 && cp <= 0xDFFF) cp = 0xFFFD;
			if (cp < 0x80) out.push_back(static_cast<char>(cp));
			else if (cp < 0x800) { out.push_back(static_cast<char>(0xC0 | (cp >> 6))); out.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
			else if (cp < 0x10000) { out.push_back(static_cast<char>(0xE0 | (cp >> 12))); out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F))); out.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
			else { out.push_back(static_cast<char>(0xF0 | (cp >> 18))); out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F))); out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F))); out.push_back(static_cast<char>(0x80 | (cp & 0x3F))); }
		}
		return out;
	}

	static std::u16string VFormat(const TCHAR* Fmt, va_list Args)
	{
		std::u16string out;
		for (const TCHAR* p = Fmt; *p; )
		{
			if (*p != u'%') { out.push_back(*p++); continue; }
			if (p[1] == u'%') { out.push_back(u'%'); p += 2; continue; }
			// 変換指定を [%flags width .prec length conv] として切り出す (数値は C の printf に任せる)。
			std::string spec = "%";
			const TCHAR* q = p + 1;
			while (*q && (*q == u'-' || *q == u'+' || *q == u' ' || *q == u'#' || *q == u'0')) spec.push_back(static_cast<char>(*q++));
			while (*q && ((*q >= u'0' && *q <= u'9') || *q == u'.')) spec.push_back(static_cast<char>(*q++));
			std::string len;
			while (*q && (*q == u'l' || *q == u'h' || *q == u'z' || *q == u'j' || *q == u't')) len.push_back(static_cast<char>(*q++));
			const TCHAR conv = *q ? *q++ : u'\0';
			char buf[512];
			switch (conv)
			{
			case u's':
			{
				const TCHAR* s = va_arg(Args, const TCHAR*);
				out += s ? std::u16string(s) : std::u16string(u"(null)");
				break;
			}
			case u'd': case u'i':
			{
				const std::string f = spec + (len.empty() ? "" : len) + "d";
				if (len == "ll" || len == "z" || len == "j" || len == "t" || len == "l") std::snprintf(buf, sizeof buf, (spec + len + "d").c_str(), va_arg(Args, long long));
				else std::snprintf(buf, sizeof buf, f.c_str(), va_arg(Args, int));
				out += Utf8ToUtf16(buf, std::strlen(buf));
				break;
			}
			case u'u': case u'x': case u'X':
			{
				const char c = static_cast<char>(conv);
				if (!len.empty()) std::snprintf(buf, sizeof buf, (spec + "ll" + c).c_str(), va_arg(Args, unsigned long long));
				else std::snprintf(buf, sizeof buf, (spec + c).c_str(), va_arg(Args, unsigned int));
				out += Utf8ToUtf16(buf, std::strlen(buf));
				break;
			}
			case u'f': case u'F': case u'e': case u'E': case u'g': case u'G':
			{
				std::snprintf(buf, sizeof buf, (spec + static_cast<char>(conv)).c_str(), va_arg(Args, double));
				out += Utf8ToUtf16(buf, std::strlen(buf));
				break;
			}
			case u'c':
				out.push_back(static_cast<TCHAR>(va_arg(Args, int)));
				break;
			default:
				return out; // 未対応の指定子: そこで打ち切る
			}
			p = q;
		}
		return out;
	}

	std::u16string FormatPrintf(const TCHAR* Fmt, ...)
	{
		va_list a; va_start(a, Fmt);
		std::u16string r = VFormat(Fmt, a);
		va_end(a);
		return r;
	}
}

FString FString::Printf(const TCHAR* Fmt, ...)
{
	va_list a; va_start(a, Fmt);
	std::u16string r = MmdShim::VFormat(Fmt, a);
	va_end(a);
	return FString(std::move(r));
}

namespace
{
#if PLATFORM_WINDOWS
	_locale_t CLocale() { static _locale_t L = _create_locale(LC_ALL, "C"); return L; }
#else
	locale_t CLocale() { static locale_t L = newlocale(LC_ALL_MASK, "C", nullptr); return L; }
#endif
	// 1 文字 1 バイトに写す (非 ASCII は 0x7F にして数値解析がそこで止まるようにする)。
	std::string Narrow(const TCHAR* S)
	{
		std::string r;
		for (; *S; ++S) r.push_back(*S < 0x80 ? static_cast<char>(*S) : '\x7f');
		return r;
	}
}

double FCString::Atod(const TCHAR* S)
{
	const std::string n = Narrow(S);
#if PLATFORM_WINDOWS
	return _strtod_l(n.c_str(), nullptr, CLocale());
#else
	return strtod_l(n.c_str(), nullptr, CLocale());
#endif
}

int32 FCString::Strtoi(const TCHAR* S, TCHAR** End, int32 Base)
{
	const std::string n = Narrow(S);
	char* e = nullptr;
	const long v = std::strtol(n.c_str(), &e, Base);
	if (End) *End = const_cast<TCHAR*>(S + (e - n.c_str()));
	return static_cast<int32>(v);
}

bool FFileHelper::LoadFileToArray(TArray<uint8>& Result, const TCHAR* Filename)
{
	std::FILE* f =
#if PLATFORM_WINDOWS
		_wfopen(reinterpret_cast<const wchar_t*>(Filename), L"rb");
#else
		std::fopen(MmdShim::Utf16ToUtf8(Filename, std::char_traits<TCHAR>::length(Filename)).c_str(), "rb");
#endif
	if (f == nullptr) return false;
	Result.Reset();
	uint8 buf[65536];
	size_t n;
	while ((n = std::fread(buf, 1, sizeof buf, f)) > 0)
	{
		const int32 old = Result.Num();
		Result.SetNum(old + static_cast<int32>(n));
		FMemory::Memcpy(Result.GetData() + old, buf, n);
	}
	const bool ok = std::ferror(f) == 0;
	std::fclose(f);
	return ok;
}

double FPlatformTime::Seconds()
{
	using namespace std::chrono;
	return duration<double>(steady_clock::now().time_since_epoch()).count();
}
