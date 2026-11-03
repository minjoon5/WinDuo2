// The engine: sensor, controller, lid switch and renderer, driven from the UI
// thread's message loop. Ported from the Python app's winduo2/app.py (in the git history).

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "../core/core.h"
#include "platform.h"
#include "renderer.h"

namespace wd {

// How each run of the effect performed; was winduo2/render/framestats.py.
class FrameStats {
public:
    void begin(double period);
    void frame(double now, double work, double upload, bool uploaded);
    void painted(double seconds);
    void presented(double seconds);
    bool active() const { return active_; }
    std::string summary();  // ends the run

private:
    bool active_ = false;
    double period_ = 1.0 / 60;
    std::vector<double> intervals_, work_, uploads_, paints_, presents_;
    bool has_last_ = false;
    double first_ = 0.0, last_ = 0.0;
};

// A scripted close and open, so the effect can be seen without a lid.
struct PreviewSweep {
    PreviewSweep(double trigger_travel, double span, double now);
    bool travel(double now, double& out) const;  // false once over
    double velocity_at(double now) const;
    double duration() const { return 2.0 * half(); }
    double half() const { return closing + hold + opening; }
    double started_at, rest = 0.0, deep, closing = 1.4, hold = 1.0, opening = 0.6;

private:
    double one_way(double elapsed) const;
    double one_way_rate(double elapsed) const;
};

class Engine {
public:
    explicit Engine(SettingsStore& store);
    ~Engine();

    void start();
    void stop();

    // The message loop: what to wait on, and for how long.
    HANDLE waitable() const;  // the swap chain's signal, or null for now
    DWORD timeout_ms() const;
    void on_waitable_signalled();  // the loop's wait returned waitable()
    void tick();
    void display_changed();  // WM_DISPLAYCHANGE and friends
    void settings_changed();

    void run_preview();
    std::string problem() const;
    double travel() const { return controller_.travel(); }
    bool is_effect_running() const { return controller_.is_active(); }
    SensorSource& sensor() { return sensor_; }
    // Set when anything a status display shows has changed; cleared by reading.
    bool take_changed();

private:
    void step(double now);
    void drive_preview(double now);
    bool present();
    void draw(const Frame& frame);
    double picture_fade(bool new_picture, double now);
    void hold_for_reopening(double now);
    bool hold_expired(double now);
    void update_prewarm(double now);
    bool ensure_capture();
    void release_capture();
    void reveal();
    void dismiss(bool animated);
    void report_run();
    void teardown_effect();
    bool make_renderer();
    void destroy_renderer();
    void raise_topmost();
    double period() const;

    SettingsStore& store_;
    Settings settings_;
    EffectController controller_;
    SensorSource sensor_;
    LidSwitch lid_;
    PointerHider pointer_;
    FrameStats stats_;
    std::unique_ptr<PreviewSweep> preview_;

    void* renderer_ = nullptr;
    WdInfo info_{};
    WdParams params_{};
    bool running_ = false;
    bool shown_ = false;
    bool revealed_ = false;
    bool has_picture_ = false;
    bool placeholder_ = false;
    bool capturing_ = false;
    double clear_at_ = 0.0;  // when a fade out ends, to drop the picture
    double raised_at_ = 0.0;
    double refresh_hz_ = 60.0;
    std::string problem_;
    bool changed_ = true;

    double prewarm_until_ = 0.0;
    bool held_ = false;
    double held_at_ = 0.0, held_open_since_ = 0.0;
    std::atomic<bool> shut_pending_{false};
    std::atomic<bool> opened_pending_{false};
    std::atomic<double> lid_opened_at_{0.0};
    double picture_fade_from_ = -1e300;
};

}  // namespace wd
