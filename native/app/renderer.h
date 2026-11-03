// The renderer's C API, from native/winduo2_native.cpp, which the app links
// in directly. The same functions the Python app once reached through ctypes.
#pragma once

#include "../core/core.h"

extern "C" {

struct WdParams {
    float profile[wd::PROFILE_SAMPLES * 2];
    float profile_end, profile_slope, max_radius, blur_strength, blur_floor, max_dim,
        dim_strength, dim_floor, dim_reach, hinge_glow, reflection_intensity, turn, picture_fade;
};

struct WdInfo {
    int screen_width, screen_height, picture_width, picture_height, texture_width,
        texture_height, inset, max_level, rotation, excluded;
};

struct WdTimings {
    float capture_ms, render_ms, present_ms;
};

enum { WD_WANT_PICTURE = 1, WD_MUST_PRESENT = 2 };
enum { WD_DREW = 1, WD_NEW_PICTURE = 2, WD_HAS_PICTURE = 4, WD_CAPTURE_LOST = 8 };

const char* wd_last_error();
void* wd_create(int width_cap);
void wd_destroy(void* handle);
void wd_info(void* handle, WdInfo* info);
void* wd_hwnd(void* handle);
int wd_capture(void* handle, int on);
int wd_frame(void* handle, const WdParams* params, int flags, WdTimings* timings);
void* wd_waitable(void* handle);
void wd_signalled(void* handle);
int wd_holds_signal(void* handle);
void wd_blank(void* handle);
int wd_clear(void* handle);
int wd_opacity(void* handle, float from, float to, float seconds);
int wd_selftest();
}
