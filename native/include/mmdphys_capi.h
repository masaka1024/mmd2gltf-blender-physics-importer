/* mmdphys C API。Blender アドオンから ctypes で呼ぶ。全関数 extern "C"。
 * 姿勢 = 7 float (px,py,pz,qx,qy,qz,qw)、raw PMX 空間。
 * 戻り値の約束:
 *   mmd_get_info / mmd_bake / mmd_live_*  : MMD_OK / MMD_WARN_NAN / MMD_ERR
 *   mmd_get_physics_bones / _rest_world / _times : 書き込んだ要素数 (ボーン数 / フレーム数、0 以上)、失敗で MMD_ERR
 * どの関数も入口で mmd_last_error() を "" に戻す。エラー文字列は UTF-8、スレッドごと。 */
#ifndef MMDPHYS_CAPI_H
#define MMDPHYS_CAPI_H

#include <stdint.h>

#if defined(_WIN32)
#  if defined(MMDPHYS_BUILD)
#    define MMD_API __declspec(dllexport)
#  else
#    define MMD_API __declspec(dllimport)
#  endif
#else
#  define MMD_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define MMD_API_VERSION 1
enum { MMD_OK = 0, MMD_WARN_NAN = 1, MMD_ERR = -1 };

typedef struct MmdHandle MmdHandle;
typedef struct { int32_t bone_count, physics_bone_count, frame_count; float unit_scale; } MmdInfo;
typedef void (*MmdProgressFn)(int32_t done, int32_t total, void* user);

MMD_API int32_t     mmd_api_version(void);
MMD_API const char* mmd_last_error(void);                       /* UTF-8、スレッドごと。エラー無しは "" (NULL にはならない) */
MMD_API MmdHandle*  mmd_create(const char* utf8_glb_path);      /* 失敗で NULL */
MMD_API void        mmd_destroy(MmdHandle*);
MMD_API int32_t     mmd_get_info(MmdHandle*, MmdInfo* out);     /* frame_count はアニメ 0 本なら 0 */
MMD_API int32_t     mmd_get_physics_bones(MmdHandle*, int32_t* out, int32_t cap);
MMD_API int32_t     mmd_get_rest_world(MmdHandle*, float* out, int32_t cap_bones);           /* [bone][7] */
MMD_API int32_t     mmd_get_times(MmdHandle*, float* out, int32_t cap_frames);
MMD_API int32_t     mmd_bake(MmdHandle*, float* out_world, int64_t cap_floats,
                             MmdProgressFn progress, void* user);                           /* [frame][bone][7] */
MMD_API int32_t     mmd_live_reset(MmdHandle*, const float* bone_world_in);                 /* [bone][7] */
/* mmd_live_step: bone_world_out は bone_world_in と同じバッファでもよい (別名可)。reset 前 / reset 失敗後は MMD_ERR。 */
MMD_API int32_t     mmd_live_step(MmdHandle*, const float* bone_world_in, float dt, float* bone_world_out);

#ifdef __cplusplus
}
#endif
#endif
