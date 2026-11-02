// A plain C API over the core, for tests/test_core_parity.py: the Python
// tests drive this and the Python implementation with the same inputs and
// compare what comes out. Not used by the app itself.

#include <cstring>
#include <string>

#include "core.h"

#if defined(_WIN32)
#define WDC_API extern "C" __declspec(dllexport)
#else
#define WDC_API extern "C" __attribute__((visibility("default")))
#endif

using namespace wd;

struct WdcSample {
    double travel, velocity, confidence, timestamp;
    int at_rest;
    double absolute_angle;
};

struct WdcFrame {
    double start_angle, current_angle, progress, velocity;
    int is_final, opening;
};

static void to_c(const AngleSample& s, WdcSample* out) {
    out->travel = s.travel;
    out->velocity = s.velocity;
    out->confidence = s.confidence;
    out->timestamp = s.timestamp;
    out->at_rest = s.at_rest ? 1 : 0;
    out->absolute_angle = s.absolute_angle;
}

static AngleSample from_c(const WdcSample* in) {
    AngleSample s;
    s.travel = in->travel;
    s.velocity = in->velocity;
    s.confidence = in->confidence;
    s.timestamp = in->timestamp;
    s.at_rest = in->at_rest != 0;
    s.absolute_angle = in->absolute_angle;
    return s;
}

// --- Settings ---------------------------------------------------------------

// The settings a JSON text gives, clamped, back as JSON. 0 if it did not parse.
WDC_API int wdc_settings_roundtrip(const char* json, char* out, int size) {
    bool ok = false;
    Settings s = Settings::from_json(json, &ok);
    std::string text = s.to_json();
    strncpy(out, text.c_str(), (size_t)size - 1);
    out[size - 1] = 0;
    return ok ? 1 : 0;
}

// --- Estimator --------------------------------------------------------------

WDC_API void* wdc_estimator_new(double settle, double tolerance, double hold, double hold_travel) {
    return new TravelEstimator(settle, tolerance, hold, hold_travel);
}
WDC_API void wdc_estimator_free(void* e) { delete static_cast<TravelEstimator*>(e); }
WDC_API void wdc_estimator_feed(void* e, double angle, double confidence, double now,
                                WdcSample* out) {
    to_c(static_cast<TravelEstimator*>(e)->feed(angle, confidence, now), out);
}
WDC_API int wdc_estimator_dropped(void* e, double now, WdcSample* out) {
    AngleSample s;
    if (!static_cast<TravelEstimator*>(e)->note_dropped_reading(now, s)) return 0;
    to_c(s, out);
    return 1;
}

// --- Geometry and gradients ---------------------------------------------------

// Fills ``table`` (2 * samples doubles); returns 0 if the sheet projects to nothing.
WDC_API int wdc_profile(double start, double current, double distance, double recession,
                        double width, double height, int samples, int expand, double* table,
                        double* end, double* slope) {
    DepthGeometry g;
    Profile p = g.profile(start, current, distance, recession, width, height, samples, expand != 0);
    if (!p.valid()) return 0;
    for (size_t i = 0; i < p.table.size(); ++i) {
        table[2 * i] = p.table[i].first;
        table[2 * i + 1] = p.table[i].second;
    }
    *end = p.end;
    *slope = p.slope;
    return 1;
}

WDC_API void wdc_corners(double start, double current, double distance, double recession,
                         double width, double height, int expand, double* out8) {
    DepthGeometry g;
    auto c = g.corners(start, current, distance, recession, width, height, expand != 0);
    for (int i = 0; i < 4; ++i) {
        out8[2 * i] = c[i].x;
        out8[2 * i + 1] = c[i].y;
    }
}

WDC_API void wdc_gradient(double progress, double velocity, double* out4) {
    BlurGradient g;
    out4[0] = g.blur_strength(progress);
    out4[1] = g.dim_strength(progress);
    out4[2] = g.turn_strength(progress);
    out4[3] = g.motion_boost(velocity);
}

WDC_API double wdc_tilt_knee(double travel, double limit) { return tilt_knee(travel, limit); }

// --- Controller ---------------------------------------------------------------

WDC_API void* wdc_controller_new(const char* settings_json) {
    return new EffectController(Settings::from_json(settings_json));
}
WDC_API void wdc_controller_free(void* c) { delete static_cast<EffectController*>(c); }
WDC_API void wdc_controller_observe(void* c, const WdcSample* sample, double now) {
    if (!sample) {
        static_cast<EffectController*>(c)->observe(nullptr, now);
        return;
    }
    AngleSample s = from_c(sample);
    static_cast<EffectController*>(c)->observe(&s, now);
}
WDC_API int wdc_controller_step(void* c, double now, WdcFrame* out) {
    Frame f;
    if (!static_cast<EffectController*>(c)->step(now, f)) return 0;
    out->start_angle = f.start_angle;
    out->current_angle = f.current_angle;
    out->progress = f.progress;
    out->velocity = f.velocity;
    out->is_final = f.is_final ? 1 : 0;
    out->opening = f.opening ? 1 : 0;
    return 1;
}
WDC_API int wdc_controller_phase(void* c) {
    return (int)static_cast<EffectController*>(c)->phase;
}
WDC_API void wdc_controller_lid_shut(void* c) { static_cast<EffectController*>(c)->lid_shut(); }
WDC_API void wdc_controller_lid_opened(void* c) { static_cast<EffectController*>(c)->lid_opened(); }
WDC_API void wdc_controller_suspend(void* c) { static_cast<EffectController*>(c)->suspend(); }
WDC_API void wdc_controller_resume(void* c) { static_cast<EffectController*>(c)->resume(); }
WDC_API double wdc_controller_poll_interval(void* c) {
    return static_cast<EffectController*>(c)->poll_interval();
}
WDC_API double wdc_controller_travel(void* c) {
    return static_cast<EffectController*>(c)->travel();
}
