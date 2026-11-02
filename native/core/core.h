// The effect's logic, independent of Windows: settings, the lid angle
// estimator, the spring, the gradients, the sheet's projection and the
// controller that decides when the effect runs.
//
// Ported from the Python package winduo2 (winduo2/angle/estimator.py,
// winduo2/effect/*.py, winduo2/config.py), which was itself ported in part
// from Mac Duo (Apache 2.0, Copyright 2026 Makito). Plain standard C++ so it
// builds on any platform; tests/test_core_parity.py builds it as a shared
// library and checks it against the Python implementation step by step.

#pragma once

#include <cstdint>
#include <deque>
#include <string>
#include <utility>
#include <vector>

namespace wd {

// Where log lines go. Set by the app; ignored when null.
extern void (*log_hook)(int level, const char* message);
void log_info(const char* format, ...);
void log_debug(const char* format, ...);

// ---------------------------------------------------------------------------
// Settings. Field for field winduo2/config.py's Settings, defaults included.

struct Settings {
    bool enabled = true;
    bool live_picture = true;
    double trigger_travel = 5.0;
    double full_effect_travel = 75.0;
    double release_hysteresis = 4.0;
    double max_blur_radius = 280.0;
    double blur_evenness = 0.0;
    double max_dim = 0.85;
    double dim_reach = 1.0;
    double hinge_glow = 0.5;
    double reflection_intensity = 0.5;
    double viewing_distance = 3.0;
    double recession = 0.8;
    double tilt_limit = 55.0;
    double neutral_settle_seconds = 0.4;
    double neutral_settle_tolerance = 1.5;
    double partial_hold_seconds = 0.4;
    double confidence_floor = 0.35;
    int render_width_cap = 2000;
    bool launch_at_login = true;
    bool show_reading = true;
    bool has_seen_intro = false;

    Settings clamped() const;

    // The JSON object config.json holds: only the keys that differ from the
    // defaults, as the Python app wrote it. Unknown keys are ignored on load.
    std::string to_json() const;
    static Settings from_json(const std::string& text, bool* ok = nullptr);
};

extern const double NEAREST_EYE;
extern const double FARTHEST_EYE;
double perspective_from_distance(double distance);
double distance_from_perspective(double perspective);

// ---------------------------------------------------------------------------
// The lid angle estimator.

struct AngleSample {
    double travel = 0.0;
    double velocity = 0.0;
    double confidence = 0.0;
    double timestamp = 0.0;
    bool at_rest = false;
    double absolute_angle = 0.0;
};

class TravelEstimator {
public:
    TravelEstimator(double settle_seconds = 0.4, double settle_tolerance = 1.5,
                    double hold_seconds = 0.4, double hold_travel = 6.0);

    void retune(double settle_seconds, double settle_tolerance, double hold_seconds,
                double hold_travel);
    void reset();
    AngleSample feed(double angle, double confidence, double now);
    // False when there was no sample to repeat yet.
    bool note_dropped_reading(double now, AngleSample& out);

    double settle_seconds, settle_tolerance, hold_seconds, hold_travel;

private:
    double unwrap(double angle);
    void blend_confidence(double target, double now);
    void update_rest(double angle, double now);
    double settle_time(double now) const;
    double velocity(double angle, double now);

    bool has_still_since_ = false;
    double still_since_ = 0.0;
    bool has_angle_ = false;
    double angle_ = 0.0;
    bool has_neutral_ = false;
    double neutral_ = 0.0;
    bool at_rest_ = false;
    std::deque<std::pair<double, double>> history_;
    std::deque<std::pair<double, double>> rest_window_;
    double confidence_ = 0.0;
    bool has_last_ = false;
    AngleSample last_;
};

// ---------------------------------------------------------------------------
// The spring, the gradients and the geometry.

class CriticallyDampedSpring {
public:
    explicit CriticallyDampedSpring(double start = 0.0, double rate = 16.0)
        : value(start), velocity(0.0), frequency(rate) {}
    double advance(double target, double dt, double target_velocity = 0.0);
    void reset(double v) {
        value = v;
        velocity = 0.0;
    }
    double value, velocity, frequency;
};

struct BlurGradient {
    double blur_curve = 1.0;
    double dim_curve = 0.7;
    double dim_hinge_floor = 0.2;
    double turn_curve = 1.8;
    double blur_strength(double progress) const;
    double dim_strength(double progress) const;
    double turn_strength(double progress) const;
    double motion_boost(double velocity) const;
};

extern const double DEFAULT_NEUTRAL_ANGLE;
constexpr int PROFILE_SAMPLES = 128;

struct Profile {
    std::vector<std::pair<double, double>> table;  // (picture row, scale)
    double end = 0.0;
    double slope = 0.0;
    double width = 0.0;
    double height = 0.0;
    bool valid() const { return end > 0.0 && table.size() >= 2; }
};

struct Point {
    double x, y;
};

struct DepthGeometry {
    double max_separation_degrees = 88.0;
    double min_depth_fraction = 0.1;
    double max_expand = 10.0;

    // Bottom-left, bottom-right, top-right, top-left; screen points, y up.
    std::vector<Point> corners(double start_angle, double current_angle,
                               double viewing_distance_ratio, double recession, double width,
                               double height, bool expand = false) const;
    // An invalid Profile when the sheet projects to nothing.
    Profile profile(double start_angle, double current_angle, double viewing_distance_ratio,
                    double recession, double width, double height,
                    int samples = PROFILE_SAMPLES, bool expand = false) const;
};

// ---------------------------------------------------------------------------
// The controller.

extern const double SHUT_ANGLE;
extern const double OPEN_ANGLE;
extern const double IDLE_INTERVAL;
extern const double ACTIVE_INTERVAL;

double tilt_knee(double travel, double limit);
double ease_in_out(double t);

enum class Phase { Idle, Running, ClosingOut };

struct Frame {
    double start_angle = 0.0;
    double current_angle = 0.0;
    double progress = 0.0;
    double velocity = 0.0;
    bool is_final = false;
    bool opening = false;
};

class EffectController {
public:
    explicit EffectController(const Settings& settings);

    void retune(const Settings& settings) { settings_ = settings; }
    void suspend();
    void resume();
    double poll_interval() const;
    double absolute_angle() const { return absolute_angle_; }
    bool is_active() const { return phase != Phase::Idle; }
    double travel() const { return raw_travel_; }

    // ``sample`` null means the sensor gave nothing.
    void observe(const AngleSample* sample, double now);
    void lid_shut();
    void lid_opened() { lid_closed_ = false; }
    // False for nothing to draw.
    bool step(double now, Frame& out);

    Profile profile(const Frame& frame, double width, double height) const;
    std::vector<Point> corners(const Frame& frame, double width, double height) const;

    Phase phase = Phase::Idle;
    DepthGeometry geometry;
    BlurGradient gradient;
    const Settings& settings() const { return settings_; }

private:
    double predicted_travel(double now, bool opening = false) const;
    double target_travel() const;
    bool opened_from_shut() const;
    bool wants_closing(double now) const;
    bool wants_opening(double now) const;
    bool wants_effect(double now) const;
    Frame frame(double signed_travel, bool is_final = false) const;
    void begin(double now);
    void resume_run(double now);
    void begin_closing_out(double now);
    void abandon();
    double delta(double now);

    Settings settings_;
    CriticallyDampedSpring travel_spring_{0.0};
    double raw_travel_ = 0.0;
    double velocity_ = 0.0;
    double confidence_ = 0.0;
    bool at_rest_ = true;
    double neutral_angle_;
    double absolute_angle_;
    double open_angle_;
    bool shut_seen_ = false;
    bool lid_closed_ = false;
    bool reopening_ = false;
    double sample_time_ = 0.0;
    double last_closing_ = -1e9;
    double last_opening_ = -1e9;
    double started_at_ = 0.0;
    double closing_out_since_ = 0.0;
    double closing_out_from_ = 0.0;
    double last_frame_time_ = 0.0;
    bool suspended_ = false;
};

}  // namespace wd
