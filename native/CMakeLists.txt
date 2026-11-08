cmake_minimum_required(VERSION 3.20)
project(mmdphys LANGUAGES CXX)
if(POLICY CMP0177)
  cmake_policy(SET CMP0177 NEW)
endif()

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
mmdphys_add_test(test_shim_math tests/test_shim_math.cpp)

# --- 層 2: エンジン単体のビット一致 (C# 参照エンジンの CSV と比較) ---
# 基準 CSV は build ディレクトリに作る。テスト GLB は環境変数で渡し、リポジトリには入れない。
set(MMD_TEST_GLB_SMALL "$ENV{MMD_TEST_GLB_SMALL}" CACHE FILEPATH "Small test GLB (never committed)")
if(NOT MMD_TEST_GLB_SMALL)
  set(MMD_TEST_GLB_SMALL "/Users/masaka1024/Desktop/IA_Conqueror_v4のコピー.glb")
endif()
add_executable(parity_engine tests/parity_engine.cpp)
target_link_libraries(parity_engine PRIVATE mmdphys_core)
target_include_directories(parity_engine PRIVATE tests)
mmdphys_fp_flags(parity_engine)
add_test(NAME parity_engine
  COMMAND bash -c "\"$0\" \"$1\" \"$2\" 300 && \"$3\" \"$1\" 300"
          "${CMAKE_CURRENT_SOURCE_DIR}/tests/make_ref_engine.sh"
          "${CMAKE_CURRENT_BINARY_DIR}/ref/ref_engine.csv"
          "${MMD_TEST_GLB_SMALL}"
          "$<TARGET_FILE:parity_engine>")
set_tests_properties(parity_engine PROPERTIES
  ENVIRONMENT "MMD_TEST_GLB_SMALL=${MMD_TEST_GLB_SMALL}" TIMEOUT 600)

# --- 層 3: FK のビット一致 (C# physbake の --dump-fk と比較) ---
set(MMD_TEST_GLB "$ENV{MMD_TEST_GLB}" CACHE FILEPATH "Large test GLB (never committed)")
if(NOT MMD_TEST_GLB)
  set(MMD_TEST_GLB "/Users/masaka1024/Downloads/IA 2/IA.glb")
endif()
add_library(mmdnative STATIC src/mmd_glb.cpp src/mmd_fk.cpp src/mmd_bake.cpp)
target_link_libraries(mmdnative PUBLIC mmdphys_core)
target_include_directories(mmdnative PUBLIC src)
mmdphys_fp_flags(mmdnative)

add_executable(test_fk_json tests/test_fk_json.cpp)
target_link_libraries(test_fk_json PRIVATE mmdnative)
mmdphys_fp_flags(test_fk_json)
add_test(NAME test_fk_json COMMAND test_fk_json)

add_executable(parity_fk tests/parity_fk.cpp)
target_link_libraries(parity_fk PRIVATE mmdnative)
target_include_directories(parity_fk PRIVATE tests)
mmdphys_fp_flags(parity_fk)

add_executable(parity_bake tests/parity_bake.cpp)
target_link_libraries(parity_bake PRIVATE mmdnative)
target_include_directories(parity_bake PRIVATE tests)
mmdphys_fp_flags(parity_bake)

# 基準 CSV (<name>.fk.csv / <name>.bake.csv) は make_ref_bake.sh で build 配下に作る (Task 6 も .bake.csv を使う)。
function(mmdphys_add_bake_parity tag glb)
  set(prefix "${CMAKE_CURRENT_BINARY_DIR}/ref/${tag}")
  add_test(NAME ref_bake_${tag}
    COMMAND "${CMAKE_CURRENT_SOURCE_DIR}/tests/make_ref_bake.sh" "${glb}" "${prefix}")
  set_tests_properties(ref_bake_${tag} PROPERTIES
    FIXTURES_SETUP ref_bake_${tag} RESOURCE_LOCK physbake_build TIMEOUT 900)
  add_test(NAME parity_fk_${tag} COMMAND parity_fk "${glb}" "${prefix}.fk.csv")
  set_tests_properties(parity_fk_${tag} PROPERTIES FIXTURES_REQUIRED ref_bake_${tag} TIMEOUT 600
    ENVIRONMENT "MMD_TEST_GLB_SMALL=${MMD_TEST_GLB_SMALL};MMD_TEST_GLB=${MMD_TEST_GLB}")
  add_test(NAME parity_bake_${tag} COMMAND parity_bake "${glb}" "${prefix}.bake.csv")
  set_tests_properties(parity_bake_${tag} PROPERTIES FIXTURES_REQUIRED ref_bake_${tag} TIMEOUT 900)
endfunction()
mmdphys_add_bake_parity(small "${MMD_TEST_GLB_SMALL}")
mmdphys_add_bake_parity(big "${MMD_TEST_GLB}")

# --- C 窓口 + 共有ライブラリ (Task 7) ---
add_library(mmdphys SHARED src/mmd_capi.cpp)
target_link_libraries(mmdphys PRIVATE mmdnative)
target_include_directories(mmdphys PUBLIC include)
target_compile_definitions(mmdphys PRIVATE MMDPHYS_BUILD)
set_target_properties(mmdphys PROPERTIES
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON
  POSITION_INDEPENDENT_CODE ON)
mmdphys_fp_flags(mmdphys)
if(APPLE)
  # ctypes から絶対パスで読むので install name は @rpath 形式にしておく。
  set_target_properties(mmdphys PROPERTIES INSTALL_NAME_DIR "@rpath" BUILD_WITH_INSTALL_NAME_DIR ON)
  # vendor / mmdnative のシンボルは公開しない (公開は mmd_* のみ)。
  target_link_options(mmdphys PRIVATE "-Wl,-exported_symbol,_mmd_*")
endif()

set(MMD_NATIVE_LIB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../mmd2gltf_blender_importer/native_lib"
    CACHE PATH "Where cmake --install puts the shared library")
if(WIN32)
  install(TARGETS mmdphys RUNTIME DESTINATION "${MMD_NATIVE_LIB_DIR}")
else()
  install(TARGETS mmdphys LIBRARY DESTINATION "${MMD_NATIVE_LIB_DIR}")
endif()
if(APPLE)
  install(CODE "
    execute_process(COMMAND codesign -s - --force \"${MMD_NATIVE_LIB_DIR}/libmmdphys.dylib\" RESULT_VARIABLE _cs)
    if(NOT _cs EQUAL 0)
      message(FATAL_ERROR \"codesign failed\")
    endif()
    execute_process(COMMAND codesign -v \"${MMD_NATIVE_LIB_DIR}/libmmdphys.dylib\" RESULT_VARIABLE _cv)
    if(NOT _cv EQUAL 0)
      message(FATAL_ERROR \"codesign -v failed\")
    endif()
  ")
endif()

add_executable(test_capi tests/test_capi.cpp)
target_link_libraries(test_capi PRIVATE mmdphys mmdnative)
target_include_directories(test_capi PRIVATE include)
mmdphys_fp_flags(test_capi)
add_test(NAME capi COMMAND test_capi)
set_tests_properties(capi PROPERTIES TIMEOUT 900
  ENVIRONMENT "MMD_TEST_GLB_SMALL=${MMD_TEST_GLB_SMALL};MMD_TEST_GLB_NOEXTRAS=/Users/masaka1024/KAIHATU/toyota-8fd25/out/toyota_8fd25.glb;MMD_TEST_GLB_NOANIM=/Users/masaka1024/KAIHATU/zatsu_anime/src_f8/work/out.glb")
