// Windows API を使う処理の窓口。実装 (mmd_win_file.cpp) は互換層 (shim) を include しない。
// shim の TCHAR / TEXT が <windows.h> と衝突するため、この 2 つを同じ翻訳単位に入れないこと。
#pragma once

#if defined(_WIN32)

#include <cstdio>

namespace mmdnative
{
	// UTF-8 のパスを UTF-16 に変換してバイナリ読み取りで開く。
	// 失敗すると nullptr。パスが UTF-8 として不正なときは invalidUtf8 を true にする。
	std::FILE* OpenFileForReadUtf8(const char* utf8Path, bool& invalidUtf8);
}

#endif
