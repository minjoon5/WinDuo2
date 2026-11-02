// See core.h. The comments explaining *why* each rule is what it is live in
// the Python this was ported from (winduo2/angle/estimator.py and
// winduo2/effect/controller.py); they are not repeated here, only the rules.

#include "core.h"

#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <sstream>

namespace wd {

void (*log_hook)(int level, const char* message) = nullptr;

static void vlog(int level, const char* format, va_list args) {
    if (!log_hook) return;
    char buffer[512];
    vsnprintf(buffer, sizeof buffer, format, args);
    log_hook(level, buffer);
}

void log_info(const char* format, ...) {
    va_list args;
    va_start(args, format);
    vlog(20, format, args);
    va_end(args);
}

void log_debug(const char* format, ...) {
    va_list args;
    va_start(args, format);
    vlog(10, format, args);
    va_end(args);
}

static const double PI = 3.14159265358979323846;

static double radians(double degrees) { return degrees * PI / 180.0; }

static double clamp(double value, double low, double high) {
    if (value != value) return low;  // NaN
    return value < low ? low : value > high ? high : value;
}

static double clamp01(double x) { return x < 0.0 ? 0.0 : x > 1.0 ? 1.0 : x; }

// Python's ``%`` for a positive divisor: never negative.
static double py_mod(double a, double b) {
    double r = std::fmod(a, b);
    if (r < 0.0) r += b;
    return r;
}

// ---------------------------------------------------------------------------
// Settings

Settings Settings::clamped() const {
    Settings out = *this;
    out.trigger_travel = clamp(out.trigger_travel, 2.0, 40.0);
    out.full_effect_travel = clamp(out.full_effect_travel, 5.0, 90.0);
    out.release_hysteresis = clamp(out.release_hysteresis, 0.5, 15.0);
    out.max_blur_radius = clamp(out.max_blur_radius, 10.0, 400.0);
    out.blur_evenness = clamp(out.blur_evenness, 0.0, 1.0);
    out.max_dim = clamp(out.max_dim, 0.0, 1.0);
    out.dim_reach = clamp(out.dim_reach, 0.2, 1.0);
    out.hinge_glow = clamp(out.hinge_glow, 0.0, 1.0);
    out.reflection_intensity = clamp(out.reflection_intensity, 0.0, 1.0);
    out.viewing_distance = clamp(out.viewing_distance, 1.0, 6.0);
    out.recession = clamp(out.recession, 0.0, 3.0);
    out.tilt_limit = clamp(out.tilt_limit, 0.0, 90.0);
    out.neutral_settle_seconds = clamp(out.neutral_settle_seconds, 0.15, 3.0);
    out.neutral_settle_tolerance = clamp(out.neutral_settle_tolerance, 0.3, 6.0);
    out.partial_hold_seconds = clamp(out.partial_hold_seconds, 0.4, 30.0);
    out.confidence_floor = clamp(out.confidence_floor, 0.0, 0.95);
    out.render_width_cap = (int)clamp((double)out.render_width_cap, 0.0, 7680.0);
    return out;
}

// The fields, for JSON, in the order the Python dataclass declares them.
enum class Kind { Bool, Double, Int };
struct Field {
    const char* name;
    Kind kind;
    size_t offset;
};

#define WD_FIELD(name, kind) {#name, kind, offsetof(Settings, name)}
static const Field FIELDS[] = {
    WD_FIELD(enabled, Kind::Bool),
    WD_FIELD(live_picture, Kind::Bool),
    WD_FIELD(trigger_travel, Kind::Double),
    WD_FIELD(full_effect_travel, Kind::Double),
    WD_FIELD(release_hysteresis, Kind::Double),
    WD_FIELD(max_blur_radius, Kind::Double),
    WD_FIELD(blur_evenness, Kind::Double),
    WD_FIELD(max_dim, Kind::Double),
    WD_FIELD(dim_reach, Kind::Double),
    WD_FIELD(hinge_glow, Kind::Double),
    WD_FIELD(reflection_intensity, Kind::Double),
    WD_FIELD(viewing_distance, Kind::Double),
    WD_FIELD(recession, Kind::Double),
    WD_FIELD(tilt_limit, Kind::Double),
    WD_FIELD(neutral_settle_seconds, Kind::Double),
    WD_FIELD(neutral_settle_tolerance, Kind::Double),
    WD_FIELD(partial_hold_seconds, Kind::Double),
    WD_FIELD(confidence_floor, Kind::Double),
    WD_FIELD(render_width_cap, Kind::Int),
    WD_FIELD(launch_at_login, Kind::Bool),
    WD_FIELD(show_reading, Kind::Bool),
    WD_FIELD(has_seen_intro, Kind::Bool),
};
#undef WD_FIELD

static std::string format_double(double value) {
    // The shortest text that reads back as the same double, as Python's repr.
    char buffer[40];
    for (int precision = 1; precision <= 17; ++precision) {
        snprintf(buffer, sizeof buffer, "%.*g", precision, value);
        if (std::strtod(buffer, nullptr) == value) break;
    }
    std::string text = buffer;
    if (text.find_first_of(".eEn") == std::string::npos) text += ".0";
    return text;
}

std::string Settings::to_json() const {
    const Settings defaults;
    const char* self = reinterpret_cast<const char*>(this);
    const char* base = reinterpret_cast<const char*>(&defaults);
    std::string out = "{";
    bool first = true;
    for (const Field& f : FIELDS) {
        std::string value;
        switch (f.kind) {
            case Kind::Bool: {
                bool v = *reinterpret_cast<const bool*>(self + f.offset);
                if (v == *reinterpret_cast<const bool*>(base + f.offset)) continue;
                value = v ? "true" : "false";
                break;
            }
            case Kind::Double: {
                double v = *reinterpret_cast<const double*>(self + f.offset);
                if (v == *reinterpret_cast<const double*>(base + f.offset)) continue;
                value = format_double(v);
                break;
            }
            case Kind::Int: {
                int v = *reinterpret_cast<const int*>(self + f.offset);
                if (v == *reinterpret_cast<const int*>(base + f.offset)) continue;
                value = std::to_string(v);
                break;
            }
        }
        out += first ? "\n  \"" : ",\n  \"";
        out += f.name;
        out += "\": ";
        out += value;
        first = false;
    }
    out += first ? "}" : "\n}";
    return out;
}

// A small JSON reader for one flat object. Values other than numbers and
// booleans (strings, nested objects, null) are skipped, as unknown keys are.
namespace {
struct Reader {
    const std::string& s;
    size_t i = 0;
    bool failed = false;

    void space() {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r'))
            ++i;
    }
    bool eat(char c) {
        space();
        if (i < s.size() && s[i] == c) {
            ++i;
            return true;
        }
        return false;
    }
    bool string(std::string& out) {
        space();
        if (i >= s.size() || s[i] != '"') return false;
        ++i;
        out.clear();
        while (i < s.size() && s[i] != '"') {
            if (s[i] == '\\' && i + 1 < s.size()) {
                ++i;
                char c = s[i];
                if (c == 'u') {
                    i += 4;  // not needed for setting names; skipped
                    ++i;
                    continue;
                }
                out += c == 'n' ? '\n' : c == 't' ? '\t' : c;
                ++i;
                continue;
            }
            out += s[i++];
        }
        if (i >= s.size()) return false;
        ++i;
        return true;
    }
    // Skips any value. Sets kind: 0 other, 1 number, 2 true, 3 false.
    bool value(int& kind, double& number) {
        space();
        if (i >= s.size()) return false;
        char c = s[i];
        if (c == '"') {
            std::string ignored;
            kind = 0;
            return string(ignored);
        }
        if (c == '{' || c == '[') {
            char open = c, close = c == '{' ? '}' : ']';
            int depth = 0;
            bool in_string = false;
            for (; i < s.size(); ++i) {
                char d = s[i];
                if (in_string) {
                    if (d == '\\') ++i;
                    else if (d == '"') in_string = false;
                } else if (d == '"') {
                    in_string = true;
                } else if (d == open) {
                    ++depth;
                } else if (d == close && --depth == 0) {
                    ++i;
                    kind = 0;
                    return true;
                }
            }
            return false;
        }
        if (s.compare(i, 4, "true") == 0) {
            i += 4;
            kind = 2;
            return true;
        }
        if (s.compare(i, 5, "false") == 0) {
            i += 5;
            kind = 3;
            return true;
        }
        if (s.compare(i, 4, "null") == 0) {
            i += 4;
            kind = 0;
            return true;
        }
        const char* start = s.c_str() + i;
        char* end = nullptr;
        number = std::strtod(start, &end);
        if (end == start) return false;
        i += (size_t)(end - start);
        kind = 1;
        return true;
    }
};
}  // namespace

Settings Settings::from_json(const std::string& text, bool* ok) {
    Settings out;
    Reader r{text};
    bool good = r.eat('{');
    if (good && !r.eat('}')) {
        do {
            std::string key;
            int kind = 0;
            double number = 0.0;
            if (!r.string(key) || !r.eat(':') || !r.value(kind, number)) {
                good = false;
                break;
            }
            for (const Field& f : FIELDS) {
                if (key != f.name) continue;
                char* self = reinterpret_cast<char*>(&out);
                switch (f.kind) {
                    case Kind::Bool:
                        if (kind == 2 || kind == 3)
                            *reinterpret_cast<bool*>(self + f.offset) = kind == 2;
                        else if (kind == 1)
                            *reinterpret_cast<bool*>(self + f.offset) = number != 0.0;
                        break;
                    case Kind::Double:
                        if (kind == 1) *reinterpret_cast<double*>(self + f.offset) = number;
                        else if (kind == 2 || kind == 3)
                            *reinterpret_cast<double*>(self + f.offset) = kind == 2 ? 1.0 : 0.0;
                        break;
                    case Kind::Int:
                        if (kind == 1)
                            *reinterpret_cast<int*>(self + f.offset) =
                                (int)clamp(number, -2147483648.0, 2147483647.0);
                        break;
                }
            }
        } while (r.eat(','));
        if (good && !r.eat('}')) good = false;
    }
    if (ok) *ok = good;
    return good ? out.clamped() : Settings();
}

const double NEAREST_EYE = 1.0;
const double FARTHEST_EYE = 6.0;

double perspective_from_distance(double distance) {
    return (FARTHEST_EYE - distance) / (FARTHEST_EYE - NEAREST_EYE);
}

double distance_from_perspective(double perspective) {
    return FARTHEST_EYE - clamp(perspective, 0.0, 1.0) * (FARTHEST_EYE - NEAREST_EYE);
}

// ---------------------------------------------------------------------------
// TravelEstimator

static const double VELOCITY_WINDOW = 0.1;
static const double CONFIDENCE_RISE = 4.0;
static const double CONFIDENCE_FALL = 12.0;
static const double PACE_LOOKBACK = 2.0;
static const double MAX_SETTLE = 3.0;

TravelEstimator::TravelEstimator(double settle_seconds_, double settle_tolerance_,
                                 double hold_seconds_, double hold_travel_)
    : settle_seconds(settle_seconds_),
      settle_tolerance(settle_tolerance_),
      hold_seconds(hold_seconds_),
      hold_travel(hold_travel_) {}

void TravelEstimator::retune(double settle_seconds_, double settle_tolerance_,
                             double hold_seconds_, double hold_travel_) {
    settle_seconds = settle_seconds_;
    settle_tolerance = settle_tolerance_;
    hold_seconds = hold_seconds_;
    hold_travel = hold_travel_;
}

void TravelEstimator::reset() {
    has_angle_ = false;
    has_neutral_ = false;
    at_rest_ = false;
    has_still_since_ = false;
    history_.clear();
    rest_window_.clear();
    confidence_ = 0.0;
    has_last_ = false;
}

AngleSample TravelEstimator::feed(double angle, double confidence, double now) {
    blend_confidence(confidence, now);
    angle = unwrap(angle);
    if (!has_neutral_) {
        neutral_ = angle;
        has_neutral_ = true;
    }
    update_rest(angle, now);
    AngleSample sample;
    sample.travel = neutral_ - angle;
    sample.velocity = velocity(angle, now);
    sample.confidence = confidence_;
    sample.timestamp = now;
    sample.at_rest = at_rest_;
    sample.absolute_angle = py_mod(angle, 360.0);
    last_ = sample;
    has_last_ = true;
    return sample;
}

bool TravelEstimator::note_dropped_reading(double now, AngleSample& out) {
    confidence_ = std::max(0.0, confidence_ - 0.15);
    if (!has_last_) return false;
    AngleSample sample = last_;
    sample.velocity = 0.0;
    sample.confidence = confidence_;
    sample.timestamp = now;
    sample.at_rest = at_rest_;
    last_ = sample;
    out = sample;
    return true;
}

double TravelEstimator::unwrap(double angle) {
    if (has_angle_) {
        while (angle - angle_ > 180.0) angle -= 360.0;
        while (angle - angle_ < -180.0) angle += 360.0;
    }
    angle_ = angle;
    has_angle_ = true;
    return angle;
}

void TravelEstimator::blend_confidence(double target, double now) {
    double last = has_last_ ? last_.timestamp : now;
    double dt = std::max(std::min(now - last, 0.5), 1e-3);
    double rate = target > confidence_ ? CONFIDENCE_RISE : CONFIDENCE_FALL;
    double step = rate * dt;
    double delta = target - confidence_;
    if (std::fabs(delta) <= step) confidence_ = target;
    else confidence_ += delta > 0 ? step : -step;
}

void TravelEstimator::update_rest(double angle, double now) {
    auto& window = rest_window_;
    window.emplace_back(now, angle);
    while (window.size() > 2 && window.front().first < now - MAX_SETTLE) window.pop_front();
    double settle = settle_time(now);
    double horizon = now - settle;

    if (confidence_ < 0.2) {
        window.clear();
        window.emplace_back(now, angle);
        at_rest_ = false;
        has_still_since_ = false;
        return;
    }

    std::vector<std::pair<double, double>> recent;
    for (const auto& entry : window)
        if (entry.first >= horizon) recent.push_back(entry);
    bool spans_the_window = now - window.front().first >= settle * 0.9;
    if (recent.size() < 3 || !spans_the_window) {
        at_rest_ = false;
        has_still_since_ = false;
        return;
    }

    double low = recent[0].second, high = recent[0].second, sum = 0.0;
    for (const auto& entry : recent) {
        low = std::min(low, entry.second);
        high = std::max(high, entry.second);
        sum += entry.second;
    }
    if (high - low > settle_tolerance) {
        at_rest_ = false;
        has_still_since_ = false;
        return;
    }

    if (!has_still_since_) {
        still_since_ = recent[0].first;
        has_still_since_ = true;
    }
    double centre = sum / (double)recent.size();
    bool held_closed = has_neutral_ && neutral_ - centre > hold_travel;
    if (held_closed && now - still_since_ < hold_seconds) {
        at_rest_ = false;
        return;
    }
    if (!at_rest_) log_debug("neutral re-zeroed at %.1f deg", centre);
    at_rest_ = true;
    neutral_ = centre;
}

double TravelEstimator::settle_time(double now) const {
    std::vector<std::pair<double, double>> lookback;
    for (const auto& entry : rest_window_)
        if (entry.first >= now - PACE_LOOKBACK) lookback.push_back(entry);
    if (lookback.size() < 2) return settle_seconds;
    double low = lookback[0].second, high = lookback[0].second;
    for (const auto& e : lookback) {
        low = std::min(low, e.second);
        high = std::max(high, e.second);
    }
    double moved = high - low;
    if (moved <= settle_tolerance) return settle_seconds;
    double last_low = -1e300, last_high = -1e300;
    for (const auto& e : lookback) {
        if (e.second == low) last_low = std::max(last_low, e.first);
        if (e.second == high) last_high = std::max(last_high, e.first);
    }
    double start = last_low < last_high ? low : high;
    double end = last_low < last_high ? high : low;
    double left = -1e300;
    for (const auto& e : lookback)
        if (e.second == start) left = std::max(left, e.first);
    double arrived = 1e300;
    for (const auto& e : lookback)
        if (e.second == end && e.first >= left) arrived = std::min(arrived, e.first);
    double pace = moved / std::max(arrived - left, 1e-3);
    double stretch = 3.0 * settle_tolerance / pace;
    return std::min(std::max(stretch, settle_seconds), MAX_SETTLE);
}

double TravelEstimator::velocity(double angle, double now) {
    auto& history = history_;
    history.emplace_back(now, -angle);
    while (history.size() > 3 && history.front().first < now - VELOCITY_WINDOW)
        history.pop_front();
    if (history.size() < 3) return 0.0;
    size_t n = history.size();
    double t0 = history.front().first;
    double mean_t = 0.0, mean_v = 0.0;
    for (const auto& e : history) {
        mean_t += e.first - t0;
        mean_v += e.second;
    }
    mean_t /= (double)n;
    mean_v /= (double)n;
    double spread = 0.0, covariance = 0.0;
    for (const auto& e : history) {
        double dt = (e.first - t0) - mean_t;
        spread += dt * dt;
        covariance += dt * (e.second - mean_v);
    }
    if (spread < 1e-9) return 0.0;
    return covariance / spread;
}

// ---------------------------------------------------------------------------
// Spring and gradients

double CriticallyDampedSpring::advance(double target, double dt, double target_velocity) {
    double f = frequency;
    if ((target - value) * target_velocity <= 0.0) target_velocity = 0.0;
    double acceleration = f * f * (target - value) - 2 * f * (velocity - target_velocity);
    velocity += acceleration * dt;
    value += velocity * dt;
    return value;
}

static const double MOTION_DEAD_ZONE = 30.0;
static const double MOTION_SATURATION_SPEED = 220.0;
static const double MOTION_MAX_BOOST = 0.35;

double BlurGradient::blur_strength(double p) const { return std::pow(clamp01(p), blur_curve); }
double BlurGradient::dim_strength(double p) const { return std::pow(clamp01(p), dim_curve); }
double BlurGradient::turn_strength(double p) const { return std::pow(clamp01(p), turn_curve); }

double BlurGradient::motion_boost(double velocity) const {
    double speed = std::fabs(velocity);
    if (speed <= MOTION_DEAD_ZONE) return 0.0;
    double span = MOTION_SATURATION_SPEED - MOTION_DEAD_ZONE;
    return clamp01((speed - MOTION_DEAD_ZONE) / span) * MOTION_MAX_BOOST;
}

// ---------------------------------------------------------------------------
// Geometry

const double DEFAULT_NEUTRAL_ANGLE = 100.0;

std::vector<Point> DepthGeometry::corners(double start_angle, double current_angle,
                                          double viewing_distance_ratio, double recession,
                                          double width, double height, bool expand) const {
    double travel = std::max(start_angle - current_angle, 0.0);
    double separation = radians(std::min(recession * travel, max_separation_degrees));
    double along = height / 2;
    double depth = std::max(height * viewing_distance_ratio, height * min_depth_fraction);
    double half = width / 2;
    double sin_sep = std::sin(separation), cos_sep = std::cos(separation);

    if (expand) {
        double denominator = depth * cos_sep - (height - along) * sin_sep;
        double reach = denominator > 0 ? depth / denominator
                                       : std::numeric_limits<double>::infinity();
        reach = std::min(reach, max_expand);
        double scale = depth / (depth + reach * height * sin_sep);
        double top = reach * height;
        return {{0.0, 0.0}, {width, 0.0}, {half + half / scale, top}, {half - half / scale, top}};
    }
    auto project = [&](double x, double y) -> Point {
        double scale = depth / (depth + y * sin_sep);
        return {half + (x - half) * scale, along + (y * cos_sep - along) * scale};
    };
    return {project(0.0, 0.0), project(width, 0.0), project(width, height),
            project(0.0, height)};
}

Profile DepthGeometry::profile(double start_angle, double current_angle,
                               double viewing_distance_ratio, double recession, double width,
                               double height, int samples, bool expand) const {
    Profile out;
    if (width <= 0 || height <= 0) return out;
    samples = std::max(samples, 2);
    double travel = std::max(start_angle - current_angle, 0.0);
    double separation = radians(std::min(recession * travel, max_separation_degrees));
    double along = height / 2;
    double depth = std::max(height * viewing_distance_ratio, height * min_depth_fraction);

    int steps = samples * 4;
    double ds = height / steps;
    double u = 0.0, b = 0.0;
    std::vector<double> arc{0.0}, rows{0.0}, scales{1.0};
    arc.reserve(steps + 1);
    rows.reserve(steps + 1);
    scales.reserve(steps + 1);
    for (int i = 0; i < steps; ++i) {
        u += std::cos(separation) * ds;
        b += std::sin(separation) * ds;
        double scale = depth / (depth + b);
        arc.push_back((i + 1) * ds / height);
        rows.push_back(std::max(along + (u - along) * scale, rows.back()));
        scales.push_back(scale);
    }
    out.width = width;
    out.height = height;

    if (expand) {
        for (int i = 0; i < samples; ++i) {
            double position = (double)i / (samples - 1) * steps;
            int j = std::min((int)position, steps - 1);
            double t = position - j;
            double row = rows[j] + (rows[j + 1] - rows[j]) * t;
            double scale = scales[j] + (scales[j + 1] - scales[j]) * t;
            out.table.emplace_back(row / height, 1.0 / std::max(scale, 1e-6));
        }
        out.end = height;
        out.slope = (rows[steps] - rows[steps - 1]) / height / ds;
        return out;
    }

    double end = rows.back();
    if (end <= 0.0) return Profile();
    int j = 0;
    for (int i = 0; i < samples; ++i) {
        double target = end * i / (samples - 1);
        while (j < steps - 1 && rows[j + 1] < target) ++j;
        double span = rows[j + 1] - rows[j];
        double t = span <= 1e-12 ? 0.0 : (target - rows[j]) / span;
        t = std::min(std::max(t, 0.0), 1.0);
        out.table.emplace_back(arc[j] + (arc[j + 1] - arc[j]) * t,
                               scales[j] + (scales[j + 1] - scales[j]) * t);
    }
    double last = rows[steps] - rows[steps - 1];
    out.end = end;
    out.slope = last > 1e-12 ? (arc[steps] - arc[steps - 1]) / last : 0.0;
    return out;
}

// ---------------------------------------------------------------------------
// Controller

static const double CLOSING_MEMORY = 1.2;
static const double PREDICTION_SPEED_FLOOR = 30.0;
static const double PREDICTION_LATENCY = 0.02;
static const double MINIMUM_DURATION = 0.3;
static const double CLOSING_OUT_DURATION = 0.6;
const double SHUT_ANGLE = 20.0;
const double OPEN_ANGLE = 45.0;
const double IDLE_INTERVAL = 1.0 / 8;
const double ACTIVE_INTERVAL = 1.0 / 60;

double tilt_knee(double travel, double limit) {
    if (travel <= 0.0) return 0.0;
    if (limit <= 0.0) return travel;
    return limit * std::tanh(travel / limit);
}

// The ease-out's speed term and the speed it carries; see controller.py.
static const double CARRY_LIMIT = 3.0;

static double carry(double t) {
    t = std::min(std::max(t, 0.0), 1.0);
    return t * (1.0 - t) * (1.0 - t) * (1.0 - t);
}

static double carried_speed(double value, double velocity) {
    if (value == 0.0 || value * velocity >= 0.0) return 0.0;
    double limit = CARRY_LIMIT * std::fabs(value) / CLOSING_OUT_DURATION;
    return std::max(-limit, std::min(limit, velocity));
}

double ease_in_out(double t) {
    t = std::min(std::max(t, 0.0), 1.0);
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
}

EffectController::EffectController(const Settings& settings)
    : settings_(settings),
      neutral_angle_(DEFAULT_NEUTRAL_ANGLE),
      absolute_angle_(DEFAULT_NEUTRAL_ANGLE),
      open_angle_(DEFAULT_NEUTRAL_ANGLE) {}

void EffectController::suspend() {
    suspended_ = true;
    abandon();
}

void EffectController::resume() {
    suspended_ = false;
    raw_travel_ = 0.0;
    velocity_ = 0.0;
    last_closing_ = -1e9;
    last_opening_ = -1e9;
    travel_spring_.reset(0.0);
}

double EffectController::poll_interval() const {
    if (phase != Phase::Idle) return ACTIVE_INTERVAL;
    if (std::fabs(velocity_) > 1.0 || std::fabs(raw_travel_) > 1.0) return ACTIVE_INTERVAL;
    return IDLE_INTERVAL;
}

void EffectController::observe(const AngleSample* sample, double now) {
    if (!sample) {
        confidence_ = 0.0;
        velocity_ = 0.0;
        return;
    }
    raw_travel_ = sample->travel;
    velocity_ = sample->velocity;
    confidence_ = sample->confidence;
    at_rest_ = sample->at_rest;
    sample_time_ = sample->timestamp;
    if (sample->absolute_angle > 0) absolute_angle_ = sample->absolute_angle;
    if (sample->absolute_angle > 0 && phase == Phase::Idle) {
        neutral_angle_ = sample->absolute_angle + sample->travel;
        if (sample->at_rest && neutral_angle_ >= OPEN_ANGLE) {
            open_angle_ = neutral_angle_;
            shut_seen_ = false;
        }
    }
    if (!sample->at_rest) {
        if (sample->travel > 0.0) last_closing_ = now;
        else if (sample->travel < 0.0) last_opening_ = now;
    }
}

void EffectController::lid_shut() {
    if (phase != Phase::Idle) log_info("lid shut, ending the effect");
    abandon();
    shut_seen_ = true;
    lid_closed_ = true;
}

double EffectController::predicted_travel(double now, bool opening) const {
    double sign = opening ? -1.0 : 1.0;
    double travel = sign * raw_travel_, speed = sign * velocity_;
    if (speed < PREDICTION_SPEED_FLOOR) return travel;
    double staleness = std::min(std::max(now - sample_time_, 0.0), 0.12);
    return travel + speed * (staleness + PREDICTION_LATENCY);
}

double EffectController::target_travel() const {
    if (reopening_) return open_angle_ - absolute_angle_;
    return raw_travel_;
}

bool EffectController::opened_from_shut() const {
    return raw_travel_ < 0.0 && (shut_seen_ || neutral_angle_ < SHUT_ANGLE);
}

bool EffectController::wants_closing(double now) const {
    if (lid_closed_) return false;
    bool closing_recently = now - last_closing_ < CLOSING_MEMORY;
    return closing_recently && predicted_travel(now) >= settings_.trigger_travel;
}

bool EffectController::wants_opening(double now) const {
    bool opening_recently = now - last_opening_ < CLOSING_MEMORY;
    return opening_recently && predicted_travel(now, true) >= settings_.trigger_travel;
}

bool EffectController::wants_effect(double now) const {
    if (!settings_.enabled || suspended_) return false;
    if (confidence_ < settings_.confidence_floor) return false;
    if (phase == Phase::Running) {
        if (now - started_at_ <= MINIMUM_DURATION) return true;
        if (reopening_ && at_rest_) return false;
        double release = settings_.trigger_travel - settings_.release_hysteresis;
        if (std::fabs(target_travel()) > release) return true;
        return !at_rest_;
    }
    return wants_closing(now) || wants_opening(now);
}

bool EffectController::step(double now, Frame& out) {
    double dt = delta(now);
    bool wanted = wants_effect(now);
    if (phase == Phase::Idle && wanted) begin(now);
    else if (phase == Phase::ClosingOut && wanted) resume_run(now);
    else if (phase == Phase::Running && !wanted) begin_closing_out(now);

    if (phase == Phase::Idle) {
        reopening_ = false;
        return false;
    }
    if (phase != Phase::ClosingOut) {
        travel_spring_.advance(target_travel(), dt, velocity_);
        out = frame(travel_spring_.value);
        return true;
    }
    double elapsed = (now - closing_out_since_) / CLOSING_OUT_DURATION;
    if (elapsed < 1.0) {
        double previous = travel_spring_.value;
        travel_spring_.reset(closing_out_from_ * (1.0 - ease_in_out(elapsed)) +
                             closing_out_speed_ * CLOSING_OUT_DURATION * carry(elapsed));
        travel_spring_.velocity = (travel_spring_.value - previous) / dt;
        out = frame(travel_spring_.value);
        return true;
    }
    travel_spring_.reset(0.0);
    phase = Phase::Idle;
    reopening_ = false;
    out = frame(0.0, true);
    return true;
}

Frame EffectController::frame(double signed_travel, bool is_final) const {
    const Settings& s = settings_;
    bool opening = signed_travel < 0.0;
    double travel = std::fabs(signed_travel);
    double span = std::max(s.full_effect_travel, 1.0);
    double past_trigger = std::max(travel - s.trigger_travel, 0.0);
    double progress = std::min(past_trigger / span, 1.0);
    double held_travel = travel <= s.trigger_travel
                             ? travel
                             : s.trigger_travel + tilt_knee(std::min(past_trigger, span), s.tilt_limit);
    Frame f;
    f.start_angle = neutral_angle_ - s.trigger_travel;
    f.current_angle = neutral_angle_ - held_travel;
    f.progress = progress;
    f.velocity = velocity_;
    f.is_final = is_final;
    f.opening = opening;
    return f;
}

Profile EffectController::profile(const Frame& f, double width, double height) const {
    return geometry.profile(f.start_angle, f.current_angle, settings_.viewing_distance,
                            settings_.recession, width, height, PROFILE_SAMPLES, f.opening);
}

std::vector<Point> EffectController::corners(const Frame& f, double width, double height) const {
    return geometry.corners(f.start_angle, f.current_angle, settings_.viewing_distance,
                            settings_.recession, width, height, f.opening);
}

void EffectController::begin(double now) {
    phase = Phase::Running;
    started_at_ = now;
    reopening_ = opened_from_shut();
    if (reopening_) {
        neutral_angle_ = open_angle_;
        shut_seen_ = false;
        lid_closed_ = false;
        log_info("effect on (reopening from shut): back to %.1f deg from %.1f", open_angle_,
                 absolute_angle_);
    }
    bool opening = target_travel() < 0.0;
    travel_spring_.reset(target_travel());
    log_info("effect on%s: travel %.1f deg, predicted %.1f, %.0f deg/s, confidence %.2f",
             opening ? " (opening)" : "", std::fabs(target_travel()),
             predicted_travel(now, opening), velocity_, confidence_);
}

void EffectController::resume_run(double now) {
    phase = Phase::Running;
    started_at_ = now;
    log_info("effect resumed: travel %.1f deg, eased %.1f", raw_travel_, travel_spring_.value);
}

void EffectController::begin_closing_out(double now) {
    phase = Phase::ClosingOut;
    closing_out_since_ = now;
    closing_out_from_ = travel_spring_.value;
    closing_out_speed_ = carried_speed(travel_spring_.value, travel_spring_.velocity);
    log_info("effect easing out: travel %.1f deg", raw_travel_);
}

void EffectController::abandon() {
    phase = Phase::Idle;
    reopening_ = false;
    travel_spring_.reset(0.0);
    last_closing_ = -1e9;
    last_opening_ = -1e9;
}

double EffectController::delta(double now) {
    double last = last_frame_time_;
    last_frame_time_ = now;
    if (last <= 0.0) return ACTIVE_INTERVAL;
    return std::min(std::max(now - last, 1.0 / 240), 1.0 / 20);
}

}  // namespace wd
