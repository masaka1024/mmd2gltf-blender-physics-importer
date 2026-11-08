cmake_minimum_required(VERSION 3.20)
project(mmdphys LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# Release は -O2 (CMake 既定の -O3 ではなく)。-ffast-math / -Ofast / /fp:fast は使わない。
if(NOT MSVC)
  set(CMAKE_CXX_FLAGS_RELEASE "-O2 -DNDEBUG")
endif()

# 取り込み元 (UE5 プラグイン側の MmdPhysicsCore)。vendor との同期検査にだけ使う。
set(MMD_UE5_CORE_DIR
    "${CMAKE_SOURCE_DIR}/../../mmd2gltf-ue5-physics-importer/Plugins/MmdPhysicsImporter/Source/MmdPhysicsCore"
    CACHE PATH "UE5 plugin MmdPhysicsCore directory (for the vendor sync check)")

set(MMD_VENDOR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/MmdPhysicsCore")

# --- 同期検査: vendor が取り込み元とずれていたら configure を止める ---
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/sync_check.py"
          "${MMD_UE5_CORE_DIR}" "${MMD_VENDOR_DIR}"
  RESULT_VARIABLE MMD_SYNC_RESULT
  OUTPUT_VARIABLE MMD_SYNC_OUT
  ERROR_VARIABLE MMD_SYNC_ERR)
message(STATUS "${MMD_SYNC_OUT}${MMD_SYNC_ERR}")
if(NOT MMD_SYNC_RESULT EQUAL 0)
  message(FATAL_ERROR "native/vendor/MmdPhysicsCore is out of sync with ${MMD_UE5_CORE_DIR}")
endif()

# --- 浮動小数点: C# エンジンと数値を bit 単位で比べるため、縮約・fast-math を禁止 ---
function(mmdphys_fp_flags target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /fp:precise)
  else()
    # clang: 乗算加算の FMA 縮約を止める。-ffast-math / -Ofast は絶対に付けない。
    target_compile_options(${target} PRIVATE -ffp-contract=off)
  endif()
endfunction()

file(GLOB MMD_VENDOR_SOURCES CONFIGURE_DEPENDS "${MMD_VENDOR_DIR}/Private/*.cpp")

add_library(mmdphys_core STATIC ${MMD_VENDOR_SOURCES} shim/MmdShim.cpp)
target_include_directories(mmdphys_core PUBLIC shim "${MMD_VENDOR_DIR}/Public")
mmdphys_fp_flags(mmdphys_core)

# テスト実行ファイルの追加用ヘルパー: mmdphys_add_test(<name> <source.cpp>) -> ctest 名は <name>
enable_testing()
function(mmdphys_add_test name source)
  add_executable(${name} ${source})
  target_link_libraries(${name} PRIVATE mmdphys_core)
  mmdphys_fp_flags(${name})
  add_test(NAME ${name} COMMAND ${name})
endfunction()

mmdphys_add_test(test_load tests/test_load.cpp)
