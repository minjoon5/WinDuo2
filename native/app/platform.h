// The Windows side of WinDuo2: paths, the log, settings on disk, the lid
// sensors and switch, starting at sign-in, the pointer, the shell flyouts,
// and the single-instance guard. Ported from the Python package's
// winduo2/log.py, config.py, angle/sensor.py, angle/lidswitch.py,
// autostart.py, render/win32.py, render/pointer.py and main.py (the ones
// other than log.py and config.py are in the git history).

#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <atomic>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

#include "../core/core.h"

namespace wd {

// --- Paths and text -----------------------------------------------------------

std::wstring app_dir();  // %APPDATA%\WinDuo2, made if missing
std::wstring exe_path();
bool read_file(const std::wstring& path, std::string& out);
bool write_file(const std::wstring& path, const void* data, size_t size);
std::string narrow(const std::wstring& text);
std::wstring widen(const std::string& text);

// --- The log -------------------------------------------------------------------
// %APPDATA%\WinDuo2\winduo2.log, rolling at 512 kB with two backups, in the
// format the Python app used, so the same Select-String commands still work.

void log_open(bool verbose);
void log_line(int level, const char* name, const char* format, ...);
#define LOGI(name, ...) ::wd::log_line(20, name, __VA_ARGS__)
#define LOGW(name, ...) ::wd::log_line(30, name, __VA_ARGS__)
#define LOGD(name, ...) ::wd::log_line(10, name, __VA_ARGS__)

// --- Settings on disk ----------------------------------------------------------

class SettingsStore {
public:
    SettingsStore();
    Settings get() const;
    // Applies ``change``, clamps, saves, and calls every listener.
    void update(const std::function<void(Settings&)>& change);
    void reset();  // factory values, keeping has_seen_intro
    void subscribe(std::function<void()> listener);

private:
    void save_locked() const;
    mutable std::mutex mutex_;
    Settings settings_;
    std::vector<std::function<void()>> listeners_;
};

// --- Process and session ------------------------------------------------------

bool claim_single_instance();  // false when another copy holds it
bool has_ui_access();
int windows_build();
bool supports_capture_exclusion();  // Windows 10 2004 or later
double seconds_since_boot();
double seconds_since_process_start();
double monotonic();  // seconds, QueryPerformanceCounter

// --- Pointer and flyouts -----------------------------------------------------

class PointerHider {
public:
    void begin();
    void watch();  // gives the pointer back once the mouse moves
    void end();
    ~PointerHider() { end(); }

private:
    bool hidden_ = false;
    bool has_position_ = false;
    POINT hidden_at_{};
};

bool close_shell_flyouts();  // only when one of them is in front

// --- Starting at sign-in ------------------------------------------------------

extern const wchar_t* AUTOSTART_FLAG;  // L"--autostart"
// Registers or removes the sign-in task; shells out, so call off the UI thread.
void autostart_sync(bool enabled);
std::wstring autostart_task_xml(const std::wstring& user, const std::wstring& command,
                                const std::wstring& arguments);

// At sign-in, waits until the taskbar and its notification area exist.
void wait_for_the_shell(double timeout_seconds);

// --- The lid switch ------------------------------------------------------------

class LidSwitch {
public:
    // ``on_change(is_open)`` runs on the switch's own thread.
    bool start(std::function<void(bool)> on_change);
    void stop();
    ~LidSwitch() { stop(); }

private:
    void run();
    static LRESULT CALLBACK proc(HWND, UINT, WPARAM, LPARAM);
    std::function<void(bool)> on_change_;
    std::thread thread_;
    std::atomic<HWND> hwnd_{nullptr};
    std::atomic<bool> available_{false};
    bool is_open_ = true;
    HANDLE ready_ = nullptr;
};

// --- The lid sensor --------------------------------------------------------------

struct SensorStatus {
    bool running = false;
    std::string problem;
    std::string name;
    double rate = 0.0;
};

// The sensor thread: opens the best hinge sensor, reads it at 60 Hz, runs the
// estimator, and keeps the newest sample for the engine to poll.
class SensorSource {
public:
    explicit SensorSource(const Settings& settings);
    ~SensorSource() { stop(); }
    void start();
    void stop();
    void retune(const Settings& settings);
    // The newest sample, or false when it is too old to act on.
    bool latest(AngleSample& out, double max_age = 0.4) const;
    SensorStatus status() const;

private:
    void run();
    mutable std::mutex mutex_;
    TravelEstimator estimator_;
    bool has_sample_ = false;
    AngleSample sample_;
    SensorStatus status_;
    std::thread thread_;
    std::atomic<bool> stop_{false};
};

// Implemented in sensor.cpp with C++/WinRT. Each returns a reader that gives
// (angle, confidence) or false, or null when the machine has no such sensor.
struct HingeReader {
    virtual ~HingeReader() = default;
    virtual const char* name() const = 0;
    virtual bool read(double& angle, double& confidence) = 0;
};
HingeReader* open_hinge_sensor();          // Windows.Devices.Sensors.HingeAngleSensor
HingeReader* open_intel_hinge_sensor();    // the Intel ISH custom sensor
void enter_winrt_thread();                  // the multithreaded apartment

extern const double MAX_LID_ANGLE;

}  // namespace wd
