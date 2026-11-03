// See engine.h. Ported from the Python app's winduo2/app.py,
// winduo2/render/framestats.py and winduo2/render/native.py; the reasons for
// each rule are in those files, in the git history.

#include "engine.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace wd {

static const double PREWARM_LINGER = 2.0;
static const double HOLD_AFTER_OPEN = 2.0;
static const double PICTURE_FADE_IN = 0.35;
static const float FADE_IN = 0.07f;
static const float FADE_OUT = 0.22f;
static const double RAISE_INTERVAL = 0.25;
static const DWORD BACKSTOP_MS = 50;
static const double IDLE_POLL = IDLE_INTERVAL * 0.5;

// ---------------------------------------------------------------------------
// FrameStats

void FrameStats::begin(double period) {
    *this = FrameStats();
    period_ = period;
    active_ = true;
}

void FrameStats::frame(double now, double work, double upload, bool uploaded) {
    if (!active_) return;
    if (has_last_) intervals_.push_back(now - last_);
    else first_ = now;
    has_last_ = true;
    last_ = now;
    work_.push_back(work);
    if (uploaded) uploads_.push_back(upload);
}

void FrameStats::painted(double seconds) {
    if (active_) paints_.push_back(seconds);
}

void FrameStats::presented(double seconds) {
    if (active_) presents_.push_back(seconds);
}

static double percentile(std::vector<double> values, double fraction) {
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    size_t index = (size_t)std::lround(fraction * (double)(values.size() - 1));
    return values[std::min(index, values.size() - 1)];
}

static std::string triple(const std::vector<double>& values) {
    if (values.empty()) return "-";
    char text[64];
    snprintf(text, sizeof text, "%.1f/%.1f/%.1f", percentile(values, 0.5) * 1000,
             percentile(values, 0.95) * 1000,
             *std::max_element(values.begin(), values.end()) * 1000);
    return text;
}

std::string FrameStats::summary() {
    active_ = false;
    size_t frames = work_.size();
    char text[600];
    if (frames < 2) {
        snprintf(text, sizeof text, "%zu frame(s)", frames);
        return text;
    }
    double span = last_ - first_;
    double fps = span > 0 ? (double)(frames - 1) / span : 0.0;
    int missed = 0;
    for (double interval : intervals_)
        if (interval >= period_ * 1.5) ++missed;
    snprintf(text, sizeof text,
             "%zu frames in %.2f s, %.0f fps of %.0f, %d missed; ms median/p95/max: "
             "interval %s, work %s, upload %s x%zu, paint %s, present %s",
             frames, span, fps, 1.0 / period_, missed, triple(intervals_).c_str(),
             triple(work_).c_str(), triple(uploads_).c_str(), uploads_.size(),
             triple(paints_).c_str(), triple(presents_).c_str());
    return text;
}

// ---------------------------------------------------------------------------
// PreviewSweep

PreviewSweep::PreviewSweep(double trigger_travel, double span, double now)
    : started_at(now), deep(trigger_travel + span * 1.15) {}

double PreviewSweep::one_way(double elapsed) const {
    if (elapsed < closing) return rest + (deep - rest) * (elapsed / closing);
    if (elapsed < closing + hold) return deep;
    double fraction = std::min((elapsed - closing - hold) / opening, 1.0);
    return deep + (rest - deep) * fraction;
}

bool PreviewSweep::travel(double now, double& out) const {
    double elapsed = now - started_at;
    if (elapsed < half()) {
        out = one_way(elapsed);
        return true;
    }
    if (elapsed < duration()) {
        out = -one_way(elapsed - half());
        return true;
    }
    return false;
}

// The rate of change of travel(): zero while the sweep holds, reversed as it
// comes back; see app.py.
double PreviewSweep::one_way_rate(double elapsed) const {
    if (elapsed < closing) return (deep - rest) / closing;
    if (elapsed < closing + hold) return 0.0;
    if (elapsed < half()) return (rest - deep) / opening;
    return 0.0;
}

double PreviewSweep::velocity_at(double now) const {
    double elapsed = now - started_at;
    if (elapsed < half()) return one_way_rate(elapsed);
    if (elapsed < duration()) return -one_way_rate(elapsed - half());
    return 0.0;
}

// ---------------------------------------------------------------------------
// Engine

Engine::Engine(SettingsStore& store)
    : store_(store), settings_(store.get()), controller_(settings_), sensor_(settings_) {}

Engine::~Engine() { stop(); }

void Engine::start() {
    if (running_) return;
    running_ = true;
    make_renderer();
    sensor_.start();
    lid_.start([this](bool is_open) {
        // On the lid switch's thread: flags only, acted on by the next tick.
        if (is_open) {
            lid_opened_at_ = monotonic();
            opened_pending_ = true;
        } else {
            shut_pending_ = true;
        }
    });
    LOGI("app", "engine started");
}

void Engine::stop() {
    if (!running_) return;
    running_ = false;
    teardown_effect();
    destroy_renderer();
    sensor_.stop();
    lid_.stop();
    LOGI("app", "engine stopped");
}

bool Engine::take_changed() {
    bool was = changed_;
    changed_ = false;
    return was;
}

std::string Engine::problem() const {
    if (!problem_.empty()) return problem_;
    SensorStatus status = sensor_.status();
    if (!status.problem.empty()) return status.problem;
    if (!status.running) return "Starting the lid sensor.";
    if (!supports_capture_exclusion())
        return "Windows 10 version 2004 or later is needed to keep the effect out of its own "
               "capture.";
    return "";
}

double Engine::period() const { return 1.0 / std::max(refresh_hz_, 30.0); }

// --- The renderer ----------------------------------------------------------------

static double current_refresh_hz() {
    DEVMODEW mode = {};
    mode.dmSize = sizeof mode;
    if (EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &mode) && mode.dmDisplayFrequency > 1)
        return (double)mode.dmDisplayFrequency;
    return 60.0;
}

bool Engine::make_renderer() {
    if (renderer_) return true;
    refresh_hz_ = current_refresh_hz();
    renderer_ = wd_create(settings_.render_width_cap);
    if (!renderer_) {
        problem_ = std::string("The graphics driver would not start the renderer: ") + wd_last_error();
        LOGW("app", "%s", problem_.c_str());
        changed_ = true;
        return false;
    }
    wd_info(renderer_, &info_);
    if (info_.rotation != 0 && info_.rotation != 1) {
        wd_destroy(renderer_);
        renderer_ = nullptr;
        problem_ = "The display is rotated; the effect is off until it is upright again.";
        LOGI("app", "%s", problem_.c_str());
        changed_ = true;
        return false;
    }
    problem_.clear();
    changed_ = true;
    LOGI("native", "native renderer ready: %dx%d, picture %dx%d, %d blur levels%s",
         info_.screen_width, info_.screen_height, info_.picture_width, info_.picture_height,
         info_.max_level, info_.excluded ? "" : ", NOT excluded from capture");
    LOGI("app", "drawing with the Direct3D 11 renderer, paced by the display (%.0f Hz)",
         refresh_hz_);
    return true;
}

void Engine::destroy_renderer() {
    release_capture();
    if (renderer_) wd_destroy(renderer_);
    renderer_ = nullptr;
    shown_ = revealed_ = has_picture_ = placeholder_ = false;
    clear_at_ = 0.0;
}

void Engine::raise_topmost() {
    raised_at_ = monotonic();
    HWND hwnd = renderer_ ? (HWND)wd_hwnd(renderer_) : nullptr;
    if (hwnd)
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                     SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW);
}

// --- The message loop ---------------------------------------------------------------

static bool moving(const EffectController& c, bool preview) {
    return preview || c.poll_interval() < IDLE_POLL;
}

HANDLE Engine::waitable() const {
    if (!running_ || !renderer_ || !moving(controller_, preview_ != nullptr)) return nullptr;
    // A signal already taken and not yet spent on a present must not be
    // waited for again: with one frame of latency it would never come.
    if (wd_holds_signal(renderer_)) return nullptr;
    return (HANDLE)wd_waitable(renderer_);
}

DWORD Engine::timeout_ms() const {
    if (!running_) return INFINITE;
    DWORD timeout;
    if (moving(controller_, preview_ != nullptr)) {
        timeout = waitable() ? BACKSTOP_MS : (DWORD)std::max(1.0, std::floor(period() * 1000));
    } else {
        timeout = (DWORD)std::lround(controller_.poll_interval() * 1000);
    }
    if (clear_at_ > 0.0) {
        double left = (clear_at_ - monotonic()) * 1000;
        timeout = std::min(timeout, (DWORD)std::max(0.0, left));
    }
    return timeout;
}

void Engine::on_waitable_signalled() {
    if (renderer_) wd_signalled(renderer_);
}

void Engine::tick() {
    if (!running_) return;
    double now = monotonic();
    if (clear_at_ > 0.0 && now >= clear_at_) {
        clear_at_ = 0.0;
        if (!shown_ && renderer_) {
            wd_clear(renderer_);
            has_picture_ = placeholder_ = false;
        }
    }
    step(now);
}

void Engine::step(double now) {
    if (preview_) {
        drive_preview(now);
    } else {
        AngleSample sample;
        controller_.observe(sensor_.latest(sample) ? &sample : nullptr, now);
    }
    if (opened_pending_.exchange(false)) controller_.lid_opened();
    if (shut_pending_.exchange(false)) {
        controller_.lid_shut();
        hold_for_reopening(now);
    }

    Frame frame;
    if (!controller_.step(now, frame)) {
        if (shown_ && (!held_ || hold_expired(now))) {
            held_ = false;
            dismiss(true);
        }
        if (held_ && lid_opened_at_.load() > held_at_)
            prewarm_until_ = std::max(prewarm_until_, now + PREWARM_LINGER);
        update_prewarm(now);
        return;
    }

    if (!shown_) {
        if (!present()) return;
        stats_.begin(period());
    } else if (held_) {
        held_ = false;
        LOGI("app", "reopening from the overlay held over the shut lid");
        pointer_.begin();
        stats_.begin(period());
    }

    draw(frame);
    pointer_.watch();

    if (frame.is_final) {
        dismiss(true);
        prewarm_until_ = now + PREWARM_LINGER;
        changed_ = true;
    }
}

void Engine::drive_preview(double now) {
    double travel = 0.0;
    if (!preview_->travel(now, travel)) {
        preview_.reset();
        return;
    }
    AngleSample s;
    s.travel = travel;
    s.velocity = preview_->velocity_at(now);
    s.confidence = 1.0;
    s.timestamp = now;
    s.at_rest = false;
    s.absolute_angle = 100.0 - travel;
    controller_.observe(&s, now);
}

void Engine::run_preview() {
    if (preview_ || controller_.is_active()) return;
    if (!make_renderer()) return;
    ensure_capture();
    preview_ = std::make_unique<PreviewSweep>(settings_.trigger_travel,
                                              settings_.full_effect_travel, monotonic());
    prewarm_until_ = preview_->started_at + preview_->duration() + 2.0;
}

// --- Presenting and drawing -----------------------------------------------------------

bool Engine::present() {
    if (!make_renderer() || !ensure_capture()) {
        controller_.suspend();
        controller_.resume();
        return false;
    }
    clear_at_ = 0.0;
    wd_opacity(renderer_, 0.0f, 0.0f, 0.0f);
    shown_ = true;
    revealed_ = false;
    raise_topmost();
    if (!has_ui_access()) close_shell_flyouts();
    pointer_.begin();
    changed_ = true;
    return true;
}

void Engine::reveal() {
    if (revealed_ || !renderer_) return;
    revealed_ = true;
    wd_opacity(renderer_, 0.0f, 1.0f, FADE_IN);
}

static double dpi_scale(void* renderer) {
    HWND hwnd = renderer ? (HWND)wd_hwnd(renderer) : nullptr;
    UINT dpi = hwnd ? GetDpiForWindow(hwnd) : 0;
    if (!dpi) dpi = GetDpiForSystem();
    return dpi ? dpi / 96.0 : 1.0;
}

double Engine::picture_fade(bool new_picture, double now) {
    if (has_picture_ && placeholder_) {
        if (!new_picture) return 0.0;
        picture_fade_from_ = now;
    }
    return std::min(std::max((now - picture_fade_from_) / PICTURE_FADE_IN, 0.0), 1.0);
}

void Engine::draw(const Frame& frame) {
    double now = monotonic();
    if (now - raised_at_ >= RAISE_INTERVAL) raise_topmost();
    const Settings& s = settings_;
    const BlurGradient& g = controller_.gradient;
    Profile profile = controller_.profile(frame, info_.screen_width, info_.screen_height);
    if (!profile.valid()) return;
    for (size_t i = 0; i < profile.table.size() && i < (size_t)PROFILE_SAMPLES; ++i) {
        params_.profile[2 * i] = (float)profile.table[i].first;
        params_.profile[2 * i + 1] = (float)profile.table[i].second;
        if (!(profile.table[i].second > 0.0)) return;  // a degenerate sheet
    }
    double boost = g.motion_boost(frame.velocity) * frame.progress;
    params_.profile_end = (float)profile.end;
    params_.profile_slope = (float)profile.slope;
    double picture_scale = (double)info_.picture_width / std::max(info_.screen_width, 1);
    params_.max_radius = (float)(s.max_blur_radius * dpi_scale(renderer_) * picture_scale);
    params_.blur_strength = (float)std::min(g.blur_strength(frame.progress) + boost, 1.0);
    params_.blur_floor = (float)s.blur_evenness;
    params_.max_dim = (float)s.max_dim;
    params_.dim_strength = (float)g.dim_strength(frame.progress);
    params_.dim_floor = (float)g.dim_hinge_floor;
    params_.dim_reach = (float)s.dim_reach;
    params_.hinge_glow = (float)s.hinge_glow;
    params_.reflection_intensity = (float)s.reflection_intensity;
    params_.turn = (float)g.turn_strength(frame.progress);

    bool live = s.live_picture && info_.excluded;
    bool wants_picture = (live || !has_picture_ || placeholder_) && ensure_capture();
    bool was_placeholder = has_picture_ && placeholder_;
    // Drawn black while only the stand-in is there; see picture_fade.
    params_.picture_fade = (float)picture_fade(false, now);

    WdTimings timings{};
    double started = monotonic();
    int flags = wd_frame(renderer_, &params_, wants_picture ? WD_WANT_PICTURE : 0, &timings);
    double finished = monotonic();

    if (flags & WD_NEW_PICTURE) {
        if (was_placeholder) picture_fade(true, finished);
        placeholder_ = false;
    }
    has_picture_ = (flags & WD_HAS_PICTURE) != 0;
    stats_.frame(finished, finished - started, timings.capture_ms / 1000.0,
                 (flags & WD_NEW_PICTURE) != 0);
    if (flags & WD_DREW) {
        stats_.painted(timings.render_ms / 1000.0);
        stats_.presented(timings.present_ms / 1000.0);
    }
    if (flags & WD_CAPTURE_LOST) capturing_ = false;
    if (has_picture_) reveal();
}

// --- Holding over a shut lid ------------------------------------------------------------

void Engine::hold_for_reopening(double now) {
    if (!(shown_ && has_picture_ && renderer_)) return;
    held_ = true;
    held_at_ = now;
    held_open_since_ = 0.0;
    wd_blank(renderer_);
    has_picture_ = true;
    placeholder_ = true;
    // Presented now: the screen keeps the last frame until something replaces
    // it, and that frame is the picture from before the lid shut.
    if (params_.profile_end > 0.0f) {
        params_.picture_fade = 0.0f;
        WdTimings timings{};
        wd_frame(renderer_, &params_, WD_MUST_PRESENT, &timings);
    }
    LOGI("app", "lid shut: holding the overlay, blacked out, for when it opens");
    report_run();
    pointer_.end();
    prewarm_until_ = 0.0;
    release_capture();
}

bool Engine::hold_expired(double now) {
    bool opened = lid_opened_at_.load() > held_at_ || controller_.absolute_angle() >= OPEN_ANGLE;
    if (!opened) {
        held_open_since_ = 0.0;
        return false;
    }
    if (held_open_since_ == 0.0) held_open_since_ = now;
    if (now - held_open_since_ < HOLD_AFTER_OPEN) return false;
    LOGI("app", "the lid opened but no reopening followed; dropping the held frame");
    return true;
}

// --- Capture -----------------------------------------------------------------------------

void Engine::update_prewarm(double now) {
    if (!settings_.enabled) {
        release_capture();
        return;
    }
    if (std::fabs(controller_.travel()) > settings_.trigger_travel * 0.35)
        prewarm_until_ = now + PREWARM_LINGER;
    if (now < prewarm_until_) ensure_capture();
    else release_capture();
}

bool Engine::ensure_capture() {
    if (capturing_) return true;
    if (!renderer_) return false;
    capturing_ = wd_capture(renderer_, 1) != 0;
    if (!capturing_) {
        problem_ = std::string("Could not capture the screen: ") + wd_last_error();
        LOGW("capture", "%s", problem_.c_str());
        changed_ = true;
        return false;
    }
    if (!problem_.empty()) {
        problem_.clear();
        changed_ = true;
    }
    LOGI("capture", "capturing %dx%d via Desktop Duplication (native), rendering at %dx%d",
         info_.screen_width, info_.screen_height, info_.picture_width, info_.picture_height);
    return true;
}

void Engine::release_capture() {
    if (capturing_ && renderer_) wd_capture(renderer_, 0);
    capturing_ = false;
}

// --- Ending ----------------------------------------------------------------------------------

void Engine::dismiss(bool animated) {
    if (!shown_ || !renderer_) return;
    shown_ = false;
    bool was_revealed = revealed_;
    revealed_ = false;
    if (animated && was_revealed) {
        wd_opacity(renderer_, 1.0f, 0.0f, FADE_OUT);
        clear_at_ = monotonic() + FADE_OUT + 0.03;
    } else {
        wd_opacity(renderer_, 0.0f, 0.0f, 0.0f);
        wd_clear(renderer_);
        has_picture_ = placeholder_ = false;
    }
    pointer_.end();
    report_run();
}

void Engine::report_run() {
    if (stats_.active()) LOGI("app", "effect run: %s", stats_.summary().c_str());
}

void Engine::teardown_effect() {
    held_ = false;
    dismiss(false);
    pointer_.end();
    report_run();
    release_capture();
    preview_.reset();
}

// --- Changes from outside ------------------------------------------------------------------

void Engine::settings_changed() {
    Settings before = settings_;
    settings_ = store_.get();
    controller_.retune(settings_);
    sensor_.retune(settings_);
    if (!settings_.enabled && controller_.is_active()) {
        controller_.suspend();
        controller_.resume();
        teardown_effect();
    }
    if (running_ && settings_.render_width_cap != before.render_width_cap &&
        !controller_.is_active()) {
        destroy_renderer();
        make_renderer();
    }
    changed_ = true;
}

void Engine::display_changed() {
    if (!running_) return;
    LOGI("app", "screen configuration changed");
    controller_.suspend();
    teardown_effect();
    destroy_renderer();
    make_renderer();
    controller_.resume();
    changed_ = true;
}

}  // namespace wd
