// Windows 専用。shim の TCHAR / TEXT と <windows.h> が衝突するので、ここでは shim を include しない。
#include "mmd_win_file.h"

#include <cstddef>
#include <cwchar>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#  define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#  define NOMINMAX
#endif
#include <windows.h>

namespace mmdnative
{
	std::FILE* OpenFileForReadUtf8(const char* utf8Path, bool& invalidUtf8)
	{
		invalidUtf8 = false;
		const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8Path, -1, nullptr, 0);
		if (n <= 0) { invalidUtf8 = true; return nullptr; }
		std::vector<wchar_t> wide(static_cast<std::size_t>(n));
		if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8Path, -1, wide.data(), n) <= 0)
		{
			invalidUtf8 = true;
			return nullptr;
		}
		return _wfopen(wide.data(), L"rb");
	}
}
