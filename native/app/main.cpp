// WinDuo2's entry point. Ported from winduo2/main.py.
//
//   WinDuo2.exe              run in the notification area
//   WinDuo2.exe --preview    play the effect once and exit
//   WinDuo2.exe --sensors    print the lid sensor's readings for 20 seconds
//   WinDuo2.exe --settings   open the settings window at launch
//   WinDuo2.exe --selftest   check the renderer on WARP and the core, then exit
//   WinDuo2.exe -v           log more

#include "engine.h"
#include "platform.h"
#include "ui.h"

#include <commctrl.h>
#include <shellapi.h>
#include <timeapi.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

using namespace wd;

namespace {

struct Options {
    bool autostart = false, preview = false, sensors = false, settings = false, selftest = false,
         verbose = false;
};

Options parse(int argc, wchar_t** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == AUTOSTART_FLAG) o.autostart = true;
        else if (a == L"--preview") o.preview = true;
        else if (a == L"--sensors") o.sensors = true;
        else if (a == L"--settings") o.settings = true;
        else if (a == L"--selftest") o.selftest = true;
        else if (a == L"-v" || a == L"--verbose") o.verbose = true;
    }
    return o;
}

// Writes to the console that started this, if any (the app has no console).
void say(const char* text) {
    // Output redirected to a file or pipe comes through the standard handle;
    // a console this attached to after starting has to be opened by name.
    static HANDLE out = [] {
        HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
        if (h && h != INVALID_HANDLE_VALUE) return h;
        return CreateFileW(L"CONOUT$", GENERIC_WRITE, FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0,
                           nullptr);
    }();
    DWORD wrote = 0;
    if (out && out != INVALID_HANDLE_VALUE) WriteFile(out, text, (DWORD)strlen(text), &wrote, nullptr);
}

int selftest() {
    int code = wd_selftest();
    std::string line = std::string("renderer: ") + wd_last_error() + "\n";
    say(line.c_str());
    // And the core, on a close it must react to.
    Settings settings;
    EffectController controller(settings);
    TravelEstimator estimator;
    bool ran = false;
    for (int i = 0; i < 180; ++i) {
        double t = 1.0 + i / 60.0, angle = i < 30 ? 110.0 : std::max(110.0 - (i - 30) * 1.5, 20.0);
        AngleSample s = estimator.feed(angle, 1.0, t);
        controller.observe(&s, t);
        Frame f;
        ran = controller.step(t, f) || ran;
    }
    say(ran ? "core: a close started the effect\n" : "core: a close did NOT start the effect\n");
    return code != 0 ? code : ran ? 0 : 20;
}

int print_sensor() {
    enter_winrt_thread();
    HingeReader* sensor = open_hinge_sensor();
    if (!sensor) sensor = open_intel_hinge_sensor();
    if (!sensor) {
        say("No lid angle sensor was found.\n");
        return 1;
    }
    char line[160];
    snprintf(line, sizeof line, "Reading the %s for 20 seconds. Move the lid.\n", sensor->name());
    say(line);
    double end = monotonic() + 20.0;
    while (monotonic() < end) {
        double angle = 0, confidence = 0;
        if (sensor->read(angle, confidence)) {
            snprintf(line, sizeof line, "%7.1f deg  confidence %.2f\n", angle, confidence);
            say(line);
        }
        Sleep(100);
    }
    delete sensor;
    return 0;
}

// The message loop: window messages, the swap chain's "draw now" signal, and
// the engine's own clock, without any of them starving the others.
int run_loop(Engine& engine) {
    double next_tick = 0.0;
    for (;;) {
        double now = monotonic();
        if (now >= next_tick) {
            engine.tick();
            now = monotonic();
            DWORD t = engine.timeout_ms();
            next_tick = t == INFINITE ? now + 3600.0 : now + t / 1000.0;
        }
        HANDLE waitable = engine.waitable();
        DWORD count = waitable ? 1 : 0;
        DWORD wait = (DWORD)std::max(0.0, (next_tick - monotonic()) * 1000.0);
        DWORD result = MsgWaitForMultipleObjectsEx(count, count ? &waitable : nullptr, wait,
                                                   QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        if (count && result == WAIT_OBJECT_0) {
            engine.on_waitable_signalled();
            next_tick = 0.0;  // draw now
        } else if (result == WAIT_OBJECT_0 + count) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                if (msg.message == WM_QUIT) return (int)msg.wParam;
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    Options options = parse(argc, argv);
    LocalFree(argv);
    AttachConsole(ATTACH_PARENT_PROCESS);

    if (options.selftest) return selftest();
    log_open(options.verbose);
    LOGI("main", "WinDuo2 starting, pid %lu%s", GetCurrentProcessId(),
         options.autostart ? " (started at sign-in)" : "");
    if (options.sensors) return print_sensor();

    if (!options.preview && !claim_single_instance()) {
        LOGI("main", "WinDuo2 is already running; this copy is leaving it be");
        return 0;
    }
    if (options.autostart) wait_for_the_shell(30.0);

    INITCOMMONCONTROLSEX controls = {sizeof controls, ICC_BAR_CLASSES | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);
    // Waits as short as a millisecond, for frames paced by the clock.
    timeBeginPeriod(1);

    SettingsStore store;
    Engine engine(store);

    if (options.preview) {
        engine.start();
        engine.run_preview();
        double end = monotonic() + 8.8;
        Tray none(store, engine);
        while (monotonic() < end) {
            HANDLE w = engine.waitable();
            DWORD n = w ? 1 : 0;
            DWORD r = MsgWaitForMultipleObjectsEx(n, n ? &w : nullptr, engine.timeout_ms(), QS_ALLINPUT,
                                                  MWMO_INPUTAVAILABLE);
            if (n && r == WAIT_OBJECT_0) engine.on_waitable_signalled();
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) DispatchMessageW(&msg);
            engine.tick();
        }
        engine.stop();
        timeEndPeriod(1);
        return 0;
    }

    // Keep the sign-in task in step with the setting, written on every start
    // so it follows the exe if it moved, and off the UI thread: it shells out.
    bool applied = store.get().launch_at_login;
    std::thread([applied] { autostart_sync(applied); }).detach();

    Tray tray(store, engine);
    if (!tray.create()) {
        MessageBoxW(nullptr, L"WinDuo2 could not create its notification area icon.", L"WinDuo2",
                    MB_ICONERROR);
        return 3;
    }
    store.subscribe([&engine, &store, &applied] {
        engine.settings_changed();
        bool wanted = store.get().launch_at_login;
        if (wanted != applied) {
            applied = wanted;
            std::thread([wanted] { autostart_sync(wanted); }).detach();
        }
    });
    engine.start();
    tray.show(!options.autostart);
    if (options.settings) tray.open_settings();

    LOGI("main", "WinDuo2 ready in %.1f s, %.0f s after the machine started%s",
         seconds_since_process_start(), seconds_since_boot(),
         has_ui_access() ? ", with uiAccess" : "");
    int code = run_loop(engine);
    engine.stop();
    timeEndPeriod(1);
    return code;
}
