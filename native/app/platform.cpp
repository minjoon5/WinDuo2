// See platform.h. The reasons behind each choice are in the Python modules
// these were ported from, in the git history; only the rules are repeated here.

#include "platform.h"

#include <shlobj.h>

#include <cstdarg>
#include <cstdio>
#include <ctime>
#include <vector>

namespace wd {

// ---------------------------------------------------------------------------
// Paths and text

std::string narrow(const std::wstring& text) {
    if (text.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), nullptr, 0,
                                   nullptr, nullptr);
    std::string out((size_t)size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.c_str(), (int)text.size(), &out[0], size, nullptr,
                        nullptr);
    return out;
}

std::wstring widen(const std::string& text) {
    if (text.empty()) return {};
    int size = MultiByteToWideChar(CP_UTF8, 0, text.c_str(), (int)text.size(), nullptr, 0);
    std::wstring out((size_t)size, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.c_str(), (int)text.size(), &out[0], size);
    return out;
}

bool read_file(const std::wstring& path, std::string& out) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_EXISTING, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    out.clear();
    char buffer[8192];
    DWORD got = 0;
    while (ReadFile(file, buffer, sizeof buffer, &got, nullptr) && got) out.append(buffer, got);
    CloseHandle(file);
    return true;
}

bool write_file(const std::wstring& path, const void* data, size_t size) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD wrote = 0;
    bool ok = WriteFile(file, data, (DWORD)size, &wrote, nullptr) && wrote == size;
    CloseHandle(file);
    return ok;
}

std::wstring app_dir() {
    static std::wstring dir = [] {
        wchar_t buffer[32768];
        DWORD n = GetEnvironmentVariableW(L"WINDUO2_HOME", buffer, 32768);
        std::wstring base;
        if (n > 0 && n < 32768) {
            base = buffer;
        } else {
            PWSTR roaming = nullptr;
            if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_RoamingAppData, 0, nullptr, &roaming))) {
                base = roaming;
                CoTaskMemFree(roaming);
            }
        }
        std::wstring path = base + L"\\WinDuo2";
        CreateDirectoryW(base.c_str(), nullptr);
        CreateDirectoryW(path.c_str(), nullptr);
        return path;
    }();
    return dir;
}

std::wstring exe_path() {
    wchar_t buffer[32768];
    DWORD n = GetModuleFileNameW(nullptr, buffer, 32768);
    return std::wstring(buffer, n);
}

// ---------------------------------------------------------------------------
// The log

static std::mutex g_log_mutex;
static FILE* g_log = nullptr;
static bool g_verbose = false;
static const long LOG_MAX = 512000;

static void log_roll_locked() {
    if (!g_log || ftell(g_log) < LOG_MAX) return;
    fclose(g_log);
    g_log = nullptr;
    std::wstring base = app_dir() + L"\\winduo2.log";
    DeleteFileW((base + L".2").c_str());
    MoveFileW((base + L".1").c_str(), (base + L".2").c_str());
    MoveFileW(base.c_str(), (base + L".1").c_str());
    g_log = _wfopen(base.c_str(), L"ab");
}

static void core_log(int level, const char* message) {
    log_line(level, "controller", "%s", message);
}

void log_open(bool verbose) {
    std::lock_guard<std::mutex> lock(g_log_mutex);
    g_verbose = verbose;
    if (!g_log) g_log = _wfopen((app_dir() + L"\\winduo2.log").c_str(), L"ab");
    if (g_log) fseek(g_log, 0, SEEK_END);
    log_hook = core_log;
}

void log_line(int level, const char* name, const char* format, ...) {
    if (level < 20 && !g_verbose) return;
    char message[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof message, format, args);
    va_end(args);
    SYSTEMTIME t;
    GetLocalTime(&t);
    char letter = level >= 40 ? 'E' : level >= 30 ? 'W' : level >= 20 ? 'I' : 'D';
    char line[1200];
    snprintf(line, sizeof line, "%04d-%02d-%02d %02d:%02d:%02d,%03d %c winduo2.%s: %s\r\n",
             t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, t.wMilliseconds, letter,
             name, message);
    std::lock_guard<std::mutex> lock(g_log_mutex);
    if (g_log) {
        fputs(line, g_log);
        fflush(g_log);
        log_roll_locked();
    }
    OutputDebugStringA(line);
}

// ---------------------------------------------------------------------------
// Settings on disk

static std::wstring config_path() { return app_dir() + L"\\config.json"; }

SettingsStore::SettingsStore() {
    std::string text;
    if (!read_file(config_path(), text)) return;
    bool ok = false;
    settings_ = Settings::from_json(text, &ok);
    if (!ok) LOGW("config", "could not read config.json, using defaults");
}

Settings SettingsStore::get() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return settings_;
}

void SettingsStore::save_locked() const {
    std::wstring path = config_path(), scratch = path + L".tmp";
    std::string text = settings_.to_json();
    if (!write_file(scratch, text.data(), text.size())) {
        LOGW("config", "could not save config.json");
        return;
    }
    if (!MoveFileExW(scratch.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING))
        LOGW("config", "could not save config.json (%lu)", GetLastError());
}

void SettingsStore::update(const std::function<void(Settings&)>& change) {
    std::vector<std::function<void()>> listeners;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        change(settings_);
        settings_ = settings_.clamped();
        save_locked();
        listeners = listeners_;
    }
    for (auto& listener : listeners) listener();
}

void SettingsStore::reset() {
    update([](Settings& s) {
        bool seen = s.has_seen_intro;
        s = Settings();
        s.has_seen_intro = seen;
    });
}

void SettingsStore::subscribe(std::function<void()> listener) {
    std::lock_guard<std::mutex> lock(mutex_);
    listeners_.push_back(std::move(listener));
}

// ---------------------------------------------------------------------------
// Process and session

bool claim_single_instance() {
    // Named per sign-in session. Held for the life of the process.
    HANDLE mutex = CreateMutexW(nullptr, FALSE, L"Local\\WinDuo2.running");
    DWORD error = GetLastError();
    if (!mutex) return error != ERROR_ACCESS_DENIED;  // a uiAccess copy's refuses us
    if (error == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        return false;
    }
    return true;
}

bool has_ui_access() {
    static int cached = -1;
    if (cached >= 0) return cached != 0;
    HANDLE token = nullptr;
    DWORD value = 0, returned = 0;
    cached = 0;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        if (GetTokenInformation(token, TokenUIAccess, &value, sizeof value, &returned))
            cached = value ? 1 : 0;
        CloseHandle(token);
    }
    return cached != 0;
}

int windows_build() {
    typedef LONG(WINAPI * RtlGetVersionFn)(OSVERSIONINFOW*);
    auto get = reinterpret_cast<RtlGetVersionFn>(
        reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion")));
    OSVERSIONINFOW info = {};
    info.dwOSVersionInfoSize = sizeof info;
    if (get && get(&info) == 0) return (int)info.dwBuildNumber;
    return 0;
}

bool supports_capture_exclusion() { return windows_build() >= 19041; }

double seconds_since_boot() { return (double)GetTickCount64() / 1000.0; }

double seconds_since_process_start() {
    FILETIME created, exited, kernel, user, now;
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user)) return 0.0;
    GetSystemTimeAsFileTime(&now);
    auto ticks = [](const FILETIME& f) {
        return ((unsigned long long)f.dwHighDateTime << 32) | f.dwLowDateTime;
    };
    return (double)(ticks(now) - ticks(created)) / 1e7;
}

double monotonic() {
    static LARGE_INTEGER frequency = [] {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return f;
    }();
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart / (double)frequency.QuadPart;
}

// ---------------------------------------------------------------------------
// The pointer

static const DWORD SYSTEM_CURSORS[] = {32512, 32513, 32514, 32515, 32516, 32642, 32643,
                                       32644, 32645, 32646, 32648, 32649, 32650, 32651};

void PointerHider::begin() {
    if (hidden_) return;
    int width = GetSystemMetrics(SM_CXCURSOR), height = GetSystemMetrics(SM_CYCURSOR);
    int stride = (width + 15) / 16 * 2;
    std::vector<BYTE> and_mask((size_t)(stride * height), 0xff), xor_mask((size_t)(stride * height), 0);
    for (DWORD id : SYSTEM_CURSORS) {
        HCURSOR blank = CreateCursor(GetModuleHandleW(nullptr), 0, 0, width, height,
                                     and_mask.data(), xor_mask.data());
        if (blank && SetSystemCursor(blank, id)) hidden_ = true;
    }
    has_position_ = hidden_ && GetCursorPos(&hidden_at_);
}

void PointerHider::watch() {
    if (!hidden_) return;
    POINT now;
    if (!GetCursorPos(&now)) return;
    if (!has_position_) {
        hidden_at_ = now;
        has_position_ = true;
        return;
    }
    if (now.x != hidden_at_.x || now.y != hidden_at_.y) end();
}

void PointerHider::end() {
    if (hidden_) SystemParametersInfoW(SPI_SETCURSORS, 0, nullptr, 0);
    hidden_ = false;
    has_position_ = false;
}

// ---------------------------------------------------------------------------
// Shell flyouts

static std::wstring foreground_process_name() {
    HWND window = GetForegroundWindow();
    if (!window) return {};
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return {};
    wchar_t buffer[1024];
    DWORD length = 1024;
    std::wstring name;
    if (QueryFullProcessImageNameW(process, 0, buffer, &length)) {
        name.assign(buffer, length);
        size_t slash = name.find_last_of(L'\\');
        if (slash != std::wstring::npos) name = name.substr(slash + 1);
        for (auto& c : name) c = (wchar_t)towlower(c);
    }
    CloseHandle(process);
    return name;
}

bool close_shell_flyouts() {
    static const wchar_t* HOSTS[] = {L"startmenuexperiencehost.exe", L"searchhost.exe",
                                     L"searchapp.exe", L"searchui.exe",
                                     L"shellexperiencehost.exe", L"shellhost.exe"};
    std::wstring name = foreground_process_name();
    bool flyout = false;
    for (auto host : HOSTS) flyout = flyout || name == host;
    if (!flyout) return false;
    INPUT inputs[2] = {};
    inputs[0].type = inputs[1].type = INPUT_KEYBOARD;
    inputs[0].ki.wVk = inputs[1].ki.wVk = VK_ESCAPE;
    inputs[1].ki.dwFlags = KEYEVENTF_KEYUP;
    UINT sent = SendInput(2, inputs, sizeof(INPUT));
    LOGI("win32", "closed a shell flyout that sat above the overlay");
    return sent == 2;
}

// ---------------------------------------------------------------------------
// Starting at sign-in

const wchar_t* AUTOSTART_FLAG = L"--autostart";

static std::wstring xml_escape(const std::wstring& text) {
    std::wstring out;
    for (wchar_t c : text) {
        switch (c) {
            case L'&': out += L"&amp;"; break;
            case L'<': out += L"&lt;"; break;
            case L'>': out += L"&gt;"; break;
            default: out += c;
        }
    }
    return out;
}

std::wstring autostart_task_xml(const std::wstring& user, const std::wstring& command,
                                const std::wstring& arguments) {
    std::wstring u = xml_escape(user);
    return L"<?xml version=\"1.0\" encoding=\"UTF-16\"?>\n"
           L"<Task version=\"1.2\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">\n"
           L"  <RegistrationInfo>\n"
           L"    <Description>Starts WinDuo2 when you sign in.</Description>\n"
           L"  </RegistrationInfo>\n"
           L"  <Triggers>\n"
           L"    <LogonTrigger>\n"
           L"      <Enabled>true</Enabled>\n"
           L"      <UserId>" + u + L"</UserId>\n"
           L"    </LogonTrigger>\n"
           L"  </Triggers>\n"
           L"  <Principals>\n"
           L"    <Principal id=\"Author\">\n"
           L"      <UserId>" + u + L"</UserId>\n"
           L"      <LogonType>InteractiveToken</LogonType>\n"
           L"      <RunLevel>LeastPrivilege</RunLevel>\n"
           L"    </Principal>\n"
           L"  </Principals>\n"
           L"  <Settings>\n"
           L"    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
           L"    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
           L"    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
           L"    <AllowHardTerminate>true</AllowHardTerminate>\n"
           L"    <StartWhenAvailable>false</StartWhenAvailable>\n"
           L"    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
           L"    <IdleSettings>\n"
           L"      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
           L"      <RestartOnIdle>false</RestartOnIdle>\n"
           L"    </IdleSettings>\n"
           L"    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
           L"    <Enabled>true</Enabled>\n"
           L"    <Hidden>false</Hidden>\n"
           L"    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
           L"    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
           L"    <Priority>4</Priority>\n"
           L"  </Settings>\n"
           L"  <Actions Context=\"Author\">\n"
           L"    <Exec>\n"
           L"      <Command>" + xml_escape(command) + L"</Command>\n"
           L"      <Arguments>" + xml_escape(arguments) + L"</Arguments>\n"
           L"    </Exec>\n"
           L"  </Actions>\n"
           L"</Task>\n";
}

// The program and arguments that start this copy at sign-in. A uiAccess copy
// goes through the shell, since Task Scheduler's plain CreateProcess is refused.
static void autostart_program(std::wstring& command, std::wstring& arguments) {
    std::wstring exe = exe_path();
    if (has_ui_access()) {
        wchar_t root[MAX_PATH];
        if (!GetEnvironmentVariableW(L"SYSTEMROOT", root, MAX_PATH)) wcscpy_s(root, L"C:\\Windows");
        command = std::wstring(root) + L"\\System32\\rundll32.exe";
        arguments = L"shell32.dll,ShellExec_RunDLL \"" + exe + L"\" " + AUTOSTART_FLAG;
    } else {
        command = exe;
        arguments = AUTOSTART_FLAG;
    }
}

static bool run_hidden(const std::wstring& command_line) {
    STARTUPINFOW si = {};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi = {};
    std::wstring line = command_line;
    if (!CreateProcessW(nullptr, &line[0], nullptr, nullptr, FALSE, CREATE_NO_WINDOW, nullptr,
                        nullptr, &si, &pi))
        return false;
    WaitForSingleObject(pi.hProcess, 30000);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return code == 0;
}

static void set_run_entry(bool enabled) {
    HKEY key;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Run", 0,
                      KEY_SET_VALUE, &key) != ERROR_SUCCESS)
        return;
    if (enabled) {
        std::wstring command, arguments;
        autostart_program(command, arguments);
        std::wstring value = L"\"" + command + L"\" " + arguments;
        RegSetValueExW(key, L"WinDuo2", 0, REG_SZ, (const BYTE*)value.c_str(),
                       (DWORD)((value.size() + 1) * sizeof(wchar_t)));
    } else {
        RegDeleteValueW(key, L"WinDuo2");
    }
    RegCloseKey(key);
}

void autostart_sync(bool enabled) {
    if (!enabled) {
        run_hidden(L"schtasks /Delete /TN WinDuo2 /F");
        set_run_entry(false);
        return;
    }
    wchar_t user[256] = L"", domain[256] = L"";
    GetEnvironmentVariableW(L"USERNAME", user, 256);
    GetEnvironmentVariableW(L"USERDOMAIN", domain, 256);
    std::wstring who = (domain[0] && user[0]) ? std::wstring(domain) + L"\\" + user : user;
    std::wstring command, arguments;
    autostart_program(command, arguments);
    std::wstring xml = autostart_task_xml(who, command, arguments);

    wchar_t temp[MAX_PATH], file[MAX_PATH];
    GetTempPathW(MAX_PATH, temp);
    GetTempFileNameW(temp, L"wd2", 0, file);
    bool registered = false;
    // UTF-16 with a byte order mark, as schtasks reads it.
    std::wstring with_bom = L"\xFEFF" + xml;
    write_file(file, with_bom.c_str(), with_bom.size() * sizeof(wchar_t));
    registered = run_hidden(L"schtasks /Create /TN WinDuo2 /XML \"" + std::wstring(file) + L"\" /F");
    DeleteFileW(file);
    if (registered) {
        set_run_entry(false);
    } else {
        LOGW("autostart", "could not register the sign-in task, using the Run key instead");
        set_run_entry(true);
    }
}

void wait_for_the_shell(double timeout_seconds) {
    double started = monotonic();
    for (;;) {
        HWND taskbar = FindWindowW(L"Shell_TrayWnd", nullptr);
        if (taskbar && FindWindowExW(taskbar, nullptr, L"TrayNotifyWnd", nullptr)) break;
        if (monotonic() - started >= timeout_seconds) {
            LOGW("main", "the taskbar is not up after %.0f s; starting anyway", timeout_seconds);
            return;
        }
        Sleep(100);
    }
    double waited = monotonic() - started;
    if (waited >= 0.1) LOGI("main", "waited %.1f s for the taskbar", waited);
}

// ---------------------------------------------------------------------------
// The lid switch

// GUID_LIDSWITCH_STATE_CHANGE
static const GUID LIDSWITCH = {0xBA3E0F4D, 0xB817, 0x4094,
                               {0xA2, 0xD1, 0xD5, 0x63, 0x79, 0xE6, 0xA0, 0xF3}};

bool LidSwitch::start(std::function<void(bool)> on_change) {
    if (thread_.joinable()) return available_;
    on_change_ = std::move(on_change);
    ready_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    thread_ = std::thread([this] { run(); });
    WaitForSingleObject(ready_, 2000);
    return available_;
}

void LidSwitch::stop() {
    HWND hwnd = hwnd_.load();
    if (hwnd) PostMessageW(hwnd, WM_CLOSE, 0, 0);
    if (thread_.joinable()) thread_.join();
    if (ready_) {
        CloseHandle(ready_);
        ready_ = nullptr;
    }
}

LRESULT CALLBACK LidSwitch::proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    auto* self = reinterpret_cast<LidSwitch*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (msg == WM_POWERBROADCAST && wparam == PBT_POWERSETTINGCHANGE && self) {
        auto* setting = reinterpret_cast<POWERBROADCAST_SETTING*>(lparam);
        if (setting->DataLength >= 1) {
            bool is_open = setting->Data[0] != 0;
            if (is_open != self->is_open_) {
                self->is_open_ = is_open;
                LOGD("lidswitch", "lid %s", is_open ? "opened" : "closed");
                if (self->on_change_) self->on_change_(is_open);
            }
        }
        return TRUE;
    }
    if (msg == WM_CLOSE) {
        DestroyWindow(hwnd);
        return 0;
    }
    if (msg == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

void LidSwitch::run() {
    WNDCLASSW wc = {};
    wc.lpfnWndProc = proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"WinDuo2LidSwitch";
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"WinDuo2", 0, 0, 0, 0, 0, nullptr, nullptr,
                                wc.hInstance, nullptr);
    if (!hwnd) {
        LOGW("lidswitch", "could not create the lid switch window");
        SetEvent(ready_);
        return;
    }
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(this));
    HPOWERNOTIFY handle = RegisterPowerSettingNotification(hwnd, &LIDSWITCH, DEVICE_NOTIFY_WINDOW_HANDLE);
    if (!handle) {
        LOGI("lidswitch", "this machine does not report a lid switch");
        DestroyWindow(hwnd);
        SetEvent(ready_);
        return;
    }
    hwnd_ = hwnd;
    available_ = true;
    SetEvent(ready_);
    LOGI("lidswitch", "listening for lid switch changes");
    MSG message;
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    UnregisterPowerSettingNotification(handle);
    hwnd_ = nullptr;
    available_ = false;
}

// ---------------------------------------------------------------------------
// The sensor thread

const double MAX_LID_ANGLE = 185.0;
static const int POLL_RATE = 60;

static void estimator_settings(const Settings& s, double out[4]) {
    out[0] = s.neutral_settle_seconds;
    out[1] = s.neutral_settle_tolerance;
    out[2] = s.partial_hold_seconds;
    out[3] = std::max(s.trigger_travel - s.release_hysteresis, 1.0);
}

SensorSource::SensorSource(const Settings& settings) { retune(settings); }

void SensorSource::retune(const Settings& settings) {
    double v[4];
    estimator_settings(settings, v);
    std::lock_guard<std::mutex> lock(mutex_);
    estimator_.retune(v[0], v[1], v[2], v[3]);
}

void SensorSource::start() {
    if (thread_.joinable()) return;
    stop_ = false;
    thread_ = std::thread([this] { run(); });
}

void SensorSource::stop() {
    stop_ = true;
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    status_ = SensorStatus();
    has_sample_ = false;
}

bool SensorSource::latest(AngleSample& out, double max_age) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!has_sample_ || monotonic() - sample_.timestamp > max_age) return false;
    out = sample_;
    return true;
}

SensorStatus SensorSource::status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return status_;
}

void SensorSource::run() {
    enter_winrt_thread();
    HingeReader* sensor = nullptr;
    HingeReader* (*openers[])() = {open_hinge_sensor, open_intel_hinge_sensor};
    const char* names[] = {"hinge angle sensor", "Intel ISH hinge sensor"};
    for (int i = 0; i < 2 && !sensor; ++i) {
        sensor = openers[i]();
        if (!sensor) LOGI("sensor", "no %s on this machine", names[i]);
    }
    if (!sensor) {
        std::lock_guard<std::mutex> lock(mutex_);
        status_.problem = "No lid angle sensor was found.";
        return;
    }
    LOGI("sensor", "reading the lid from the %s", sensor->name());
    {
        std::lock_guard<std::mutex> lock(mutex_);
        status_ = SensorStatus();
        status_.running = true;
        status_.name = sensor->name();
    }
    const double interval = 1.0 / POLL_RATE;
    int misses = 0, window_count = 0;
    double window_started = monotonic(), rate = 0.0;
    while (!stop_) {
        double started = monotonic();
        double angle = 0.0, confidence = 0.0;
        bool ok = false;
        try {
            ok = sensor->read(angle, confidence);
        } catch (...) {
            ok = false;
        }
        double now = monotonic();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!ok) {
                ++misses;
                AngleSample s;
                if (estimator_.note_dropped_reading(now, s)) {
                    sample_ = s;
                    has_sample_ = true;
                }
                if (misses > POLL_RATE * 2) {
                    status_ = SensorStatus();
                    status_.problem = "The sensor stopped answering.";
                    status_.name = sensor->name();
                    break;
                }
            } else {
                misses = 0;
                ++window_count;
                if (now - window_started >= 1.0) {
                    rate = window_count / (now - window_started);
                    window_started = now;
                    window_count = 0;
                }
                sample_ = estimator_.feed(angle, confidence, now);
                has_sample_ = true;
                status_.running = true;
                status_.rate = rate;
            }
        }
        double wait = std::max(interval - (monotonic() - started), 0.001);
        Sleep((DWORD)std::max(1.0, wait * 1000.0));
    }
    delete sensor;
    LOGI("sensor", "sensor closed");
}

}  // namespace wd
