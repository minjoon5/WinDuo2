// See ui.h.

#include "ui.h"

#include <commctrl.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <uxtheme.h>

#include <cmath>
#include <cstdio>
#include <vector>

#include "resource.h"

namespace wd {

static const UINT WM_TRAY = WM_APP + 1;
enum {
    ID_EFFECT = 100,
    ID_PREVIEW,
    ID_SETTINGS,
    ID_STATUS,
    ID_QUIT,
};
static const UINT_PTR TIMER_REFRESH = 1;
static const UINT_PTR TIMER_INTRO = 2;

// ---------------------------------------------------------------------------
// The settings window

namespace {

struct SliderSpec {
    const wchar_t* title;
    double minimum, maximum;
    const wchar_t* suffix;
    int decimals;
    double display_scale;
    int steps;
    const wchar_t* help;
    double (*get)(const Settings&);
    void (*set)(Settings&, double);
};

#define WD_GET(field) [](const Settings& s) -> double { return (double)s.field; }
#define WD_SET(field, type) [](Settings& s, double v) { s.field = (type)v; }

const SliderSpec SLIDERS[] = {
    {L"Fades in after", 2, 40, L"°", 0, 1, 200,
     L"How far you can close before anything happens.", WD_GET(trigger_travel),
     WD_SET(trigger_travel, double)},
    {L"Reaches full strength over", 5, 90, L"°", 0, 1, 200,
     L"Degrees of further closing to go from just visible to full.",
     WD_GET(full_effect_travel), WD_SET(full_effect_travel, double)},
    {L"Blur", 10, 400, L" px", 0, 1, 390, L"Blur radius at the top edge, furthest from the hinge.",
     WD_GET(max_blur_radius), WD_SET(max_blur_radius, double)},
    {L"Blur spread", 0, 1, L"%", 0, 100, 200,
     L"0 blurs the top edge only, 100 blurs the whole picture.", WD_GET(blur_evenness),
     WD_SET(blur_evenness, double)},
    {L"Dimming", 0, 1, L"%", 0, 100, 200, L"How dark the top edge goes.", WD_GET(max_dim),
     WD_SET(max_dim, double)},
    {L"Dimming spread", 0.2, 1, L"%", 0, 100, 200,
     L"Height where the darkening reaches full strength. 100 darkens the top edge most and "
     L"fades smoothly down to the hinge.",
     WD_GET(dim_reach), WD_SET(dim_reach, double)},
    {L"Hinge glow", 0, 1, L"%", 0, 100, 200,
     L"A narrow highlight along the hinge, as it catches light while turning.",
     WD_GET(hinge_glow), WD_SET(hinge_glow, double)},
    {L"Reflection", 0, 1, L"%", 0, 100, 200,
     L"A soft pale band partway up the picture, standing in for the glass.",
     WD_GET(reflection_intensity), WD_SET(reflection_intensity, double)},
    {L"Lean back", 0, 3, L"×", 1, 1, 200,
     L"Degrees of lean per degree of closing. Higher drops the top of the picture further.",
     WD_GET(recession), WD_SET(recession, double)},
    {L"Perspective", 0, 1, L"%", 0, 100, 200,
     L"0 keeps the sides parallel, 100 converges sharply.",
     [](const Settings& s) { return perspective_from_distance(s.viewing_distance); },
     [](Settings& s, double v) { s.viewing_distance = distance_from_perspective(v); }},
    {L"Tilt limit", 0, 90, L"°", 0, 1, 200,
     L"The most the picture tilts, however far the lid closes. 0 lets it follow the lid all "
     L"the way.",
     WD_GET(tilt_limit), WD_SET(tilt_limit, double)},
    {L"Required confidence", 0, 0.95, L"%", 0, 100, 200,
     L"The effect stays off below this, such as while the laptop is being carried or stands "
     L"on its side.",
     WD_GET(confidence_floor), WD_SET(confidence_floor, double)},
    {L"Treat as resting after", 0.15, 3.0, L" s", 2, 1, 285,
     L"How long the lid must hold still before that position becomes the new neutral.",
     WD_GET(neutral_settle_seconds), WD_SET(neutral_settle_seconds, double)},
    {L"Hold a partly closed lid for", 0.4, 30.0, L" s", 1, 1, 200,
     L"How long the effect stays when you stop partway through closing, before that position "
     L"becomes the new neutral.",
     WD_GET(partial_hold_seconds), WD_SET(partial_hold_seconds, double)},
    {L"Capture width", 640, 3840, L" px", 0, 1, 200,
     L"Lower is smoother on a high-resolution screen, and the picture is blurring anyway.",
     [](const Settings& s) { return s.render_width_cap ? (double)s.render_width_cap : 2000.0; },
     [](Settings& s, double v) { s.render_width_cap = (int)std::lround(v); }},
};
const int SLIDER_COUNT = (int)(sizeof SLIDERS / sizeof SLIDERS[0]);

// Where each section starts, by the index of its first slider.
struct Section {
    int first_slider;
    const wchar_t* title;
    const wchar_t* intro;
};
const Section SECTIONS[] = {
    {0, L"How it follows the lid",
     L"The effect follows the lid the whole way down. It fades in once you have closed a "
     L"little, then keeps growing as you keep closing, and reverses if you open again."},
    {2, L"Look", nullptr},
    {8, L"Perspective", nullptr},
    {11, L"Reading", nullptr},
};

struct Check {
    const wchar_t* title;
    const wchar_t* help;
    bool (*get)(const Settings&);
    void (*set)(Settings&, bool);
};
const Check CHECKS[] = {
    {L"Run the effect", L"Leans the screen away as you close the lid.",
     [](const Settings& s) { return s.enabled; }, [](Settings& s, bool v) { s.enabled = v; }},
    {L"Keep the picture live", L"Off holds the frame from the moment the effect started.",
     [](const Settings& s) { return s.live_picture; },
     [](Settings& s, bool v) { s.live_picture = v; }},
    {L"Start with Windows", L"Runs WinDuo2 when you sign in.",
     [](const Settings& s) { return s.launch_at_login; },
     [](Settings& s, bool v) { s.launch_at_login = v; }},
};
const int CHECK_COUNT = 3;

enum {
    IDC_CHECK = 1000,   // + index
    IDC_SLIDER = 1100,  // + index
    IDC_PREVIEW_BUTTON = 1200,
    IDC_RESET,
    IDC_DONE,
};

// --- The glass -------------------------------------------------------------
//
// The settings window is see-through acrylic, Windows' own blur, over the
// title bar and the whole client area alike. DWM shows the backdrop wherever
// the window's pixels are transparent, but GDI and the common controls draw
// opaque pixels with no alpha. So everything is drawn on white into a buffer
// first, and the white is then taken out: each pixel becomes the colour that,
// laid over white, gives what was drawn. White turns fully transparent, text
// and the controls keep their look, and the light acrylic shows behind them.

// Raw values, so an older SDK still builds; the calls fail harmlessly on an
// older Windows, which then keeps the plain white window.
const DWORD WD_DWMWA_USE_IMMERSIVE_DARK_MODE = 20;
const DWORD WD_DWMWA_SYSTEMBACKDROP_TYPE = 38;
const int WD_DWMSBT_TRANSIENTWINDOW = 3;  // acrylic
const int BUILD_FOR_BACKDROP = 22621;     // Windows 11 22H2
const DWORD ZBID_DESKTOP = 1;

void white_to_alpha(HPAINTBUFFER buffer) {
    RGBQUAD* bits = nullptr;
    int row = 0;
    RECT target;
    if (FAILED(GetBufferedPaintBits(buffer, &bits, &row)) ||
        FAILED(GetBufferedPaintTargetRect(buffer, &target)))
        return;
    int width = target.right - target.left, height = target.bottom - target.top;
    for (int y = 0; y < height; ++y) {
        RGBQUAD* p = bits + (size_t)y * row;
        for (int x = 0; x < width; ++x, ++p) {
            int darkest = std::min(std::min((int)p->rgbRed, (int)p->rgbGreen), (int)p->rgbBlue);
            int lift = darkest;  // how much of the white shows through: 255 - alpha
            p->rgbRed = (BYTE)(p->rgbRed - lift);
            p->rgbGreen = (BYTE)(p->rgbGreen - lift);
            p->rgbBlue = (BYTE)(p->rgbBlue - lift);
            p->rgbReserved = (BYTE)(255 - lift);
        }
    }
}

// Every control in the window paints through here once the glass is on.
LRESULT CALLBACK glass_control_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam,
                                    UINT_PTR id, DWORD_PTR) {
    switch (msg) {
        case WM_ERASEBKGND:
            return 1;  // painted on white with the rest, in WM_PAINT
        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC dc = BeginPaint(hwnd, &ps);
            RECT client;
            GetClientRect(hwnd, &client);
            HDC buffered = nullptr;
            HPAINTBUFFER buffer =
                BeginBufferedPaint(dc, &client, BPBF_TOPDOWNDIB, nullptr, &buffered);
            if (buffer) {
                FillRect(buffered, &client, (HBRUSH)GetStockObject(WHITE_BRUSH));
                DefSubclassProc(hwnd, WM_PRINTCLIENT, (WPARAM)buffered,
                                PRF_CLIENT | PRF_ERASEBKGND);
                white_to_alpha(buffer);
                EndBufferedPaint(buffer, TRUE);
            }
            EndPaint(hwnd, &ps);
            return 0;
        }
        case WM_NCDESTROY:
            RemoveWindowSubclass(hwnd, glass_control_proc, id);
            break;
    }
    LRESULT result = DefSubclassProc(hwnd, msg, wparam, lparam);
    // Some controls draw a state change straight to the screen rather than
    // through WM_PAINT, which would leave it without alpha; paint it again.
    switch (msg) {
        case WM_LBUTTONDOWN:
        case WM_LBUTTONUP:
        case WM_MOUSEMOVE:
        case WM_MOUSELEAVE:
        case WM_SETFOCUS:
        case WM_KILLFOCUS:
        case WM_KEYDOWN:
        case WM_KEYUP:
        case WM_CAPTURECHANGED:
        case WM_ENABLE:
        case WM_UPDATEUISTATE:
        case WM_SETTEXT:
        case BM_SETSTATE:
        case BM_SETCHECK:
            InvalidateRect(hwnd, nullptr, FALSE);
            break;
    }
    return result;
}

BOOL CALLBACK glass_control(HWND child, LPARAM skip) {
    if (child != reinterpret_cast<HWND>(skip))
        SetWindowSubclass(child, glass_control_proc, 2, 0);
    return TRUE;
}

bool high_contrast() {
    HIGHCONTRASTW hc = {};
    hc.cbSize = sizeof hc;
    return SystemParametersInfoW(SPI_GETHIGHCONTRAST, sizeof hc, &hc, 0) &&
           (hc.dwFlags & HCF_HIGHCONTRASTON);
}

}  // namespace

class SettingsWindow {
public:
    SettingsWindow(SettingsStore& store, Engine& engine) : store_(store), engine_(engine) {}
    ~SettingsWindow() {
        if (hwnd_) DestroyWindow(hwnd_);
        destroy_fonts();
        if (painting_) BufferedPaintUnInit();
    }

    void show();
    HWND hwnd() const { return hwnd_; }
    void refresh_status();
    void load();

private:
    static LRESULT CALLBACK proc(HWND, UINT, WPARAM, LPARAM);
    static LRESULT CALLBACK slider_proc(HWND, UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR);
    LRESULT handle(HWND, UINT, WPARAM, LPARAM);
    void create_controls();
    void layout();       // the header, the footer and the body between them
    void layout_body();  // the controls inside the scrolling body
    void make_fonts();
    void destroy_fonts();
    int px(double dips) const { return (int)std::lround(dips * dpi_ / 96.0); }
    HWND label(HWND parent, const wchar_t* text, HFONT font);
    void set_value_text(int index);
    double slider_value(int index) const;
    void scroll_to(int offset);
    HWND create_frame(HINSTANCE instance);
    void make_glass();
    void paint(HWND hwnd);

    SettingsStore& store_;
    Engine& engine_;
    HWND hwnd_ = nullptr;
    // Everything but the reading and the buttons sits in ``body_``, which
    // scrolls; the reading stays at the top and the buttons at the bottom.
    HWND body_ = nullptr;
    UINT dpi_ = 96;
    int scroll_ = 0, content_height_ = 0, header_height_ = 0, footer_height_ = 0;
    bool loading_ = false;
    bool glass_ = false, painting_ = false;
    HFONT font_ = nullptr, small_ = nullptr, heading_ = nullptr, big_ = nullptr;
    HBRUSH background_ = nullptr;

    HWND checks_[CHECK_COUNT] = {}, check_help_[CHECK_COUNT] = {};
    HWND sensor_heading_ = nullptr, sensor_state_ = nullptr, reading_ = nullptr,
         reading_caption_ = nullptr, problem_ = nullptr, preview_ = nullptr;
    HWND section_titles_[4] = {}, section_intro_ = nullptr;
    HWND slider_titles_[SLIDER_COUNT] = {}, slider_values_[SLIDER_COUNT] = {},
         sliders_[SLIDER_COUNT] = {}, slider_help_[SLIDER_COUNT] = {};
    HWND reset_ = nullptr, done_ = nullptr;
};

static const wchar_t* SETTINGS_CLASS = L"WinDuo2Settings";
static const wchar_t* SETTINGS_BODY_CLASS = L"WinDuo2SettingsBody";

void SettingsWindow::show() {
    if (!hwnd_) {
        WNDCLASSEXW wc = {};
        wc.cbSize = sizeof wc;
        wc.lpfnWndProc = proc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = SETTINGS_CLASS;
        wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
        wc.hIcon = LoadIconW(wc.hInstance, MAKEINTRESOURCEW(IDI_APP));
        wc.hbrBackground = (HBRUSH)GetStockObject(WHITE_BRUSH);
        RegisterClassExW(&wc);
        wc.lpszClassName = SETTINGS_BODY_CLASS;
        wc.hIcon = nullptr;
        RegisterClassExW(&wc);
        dpi_ = GetDpiForSystem();
        hwnd_ = create_frame(wc.hInstance);
        if (!hwnd_) return;
        dpi_ = GetDpiForWindow(hwnd_);
        body_ = CreateWindowExW(WS_EX_CONTROLPARENT, SETTINGS_BODY_CLASS, L"",
                                WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_CLIPCHILDREN, 0, 0, 10, 10,
                                hwnd_, nullptr, wc.hInstance, this);
        make_fonts();
        create_controls();
        make_glass();
        load();
        layout();
    }
    ShowWindow(hwnd_, SW_SHOWNORMAL);
    // The first frame painted now, so Windows' opening animation plays on the
    // finished window rather than on an empty one.
    UpdateWindow(hwnd_);
    SetForegroundWindow(hwnd_);
    refresh_status();
}

// The window itself. A uiAccess process's windows can land in the uiAccess
// z-order band, where Windows plays none of its opening and closing
// animations; the settings window is an ordinary window, so it is made in the
// desktop band like any other app's when that can be asked for.
HWND SettingsWindow::create_frame(HINSTANCE instance) {
    const DWORD style = WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN;
    if (has_ui_access()) {
        typedef HWND(WINAPI * CreateWindowInBandFn)(DWORD, LPCWSTR, LPCWSTR, DWORD, int, int, int,
                                                    int, HWND, HMENU, HINSTANCE, LPVOID, DWORD);
        auto in_band = reinterpret_cast<CreateWindowInBandFn>(reinterpret_cast<void*>(
            GetProcAddress(GetModuleHandleW(L"user32.dll"), "CreateWindowInBand")));
        if (in_band) {
            HWND made = in_band(0, SETTINGS_CLASS, L"WinDuo2 settings", style, CW_USEDEFAULT,
                                CW_USEDEFAULT, px(560), px(760), nullptr, nullptr, instance, this,
                                ZBID_DESKTOP);
            if (made) return made;
            LOGW("ui", "the settings window could not be made in the desktop band (%lu)",
                 GetLastError());
            hwnd_ = nullptr;  // in case creation got as far as WM_NCCREATE
        }
    }
    return CreateWindowExW(0, SETTINGS_CLASS, L"WinDuo2 settings", style, CW_USEDEFAULT,
                           CW_USEDEFAULT, px(560), px(760), nullptr, nullptr, instance, this);
}

// Windows' own animations on, and the acrylic backdrop behind everything.
void SettingsWindow::make_glass() {
    BOOL off = FALSE;
    DwmSetWindowAttribute(hwnd_, DWMWA_TRANSITIONS_FORCEDISABLED, &off, sizeof off);
    if (windows_build() < BUILD_FOR_BACKDROP || high_contrast()) return;
    painting_ = SUCCEEDED(BufferedPaintInit());
    if (!painting_) return;
    // A light backdrop: the window's colours are drawn for one.
    DwmSetWindowAttribute(hwnd_, WD_DWMWA_USE_IMMERSIVE_DARK_MODE, &off, sizeof off);
    int backdrop = WD_DWMSBT_TRANSIENTWINDOW;
    if (FAILED(DwmSetWindowAttribute(hwnd_, WD_DWMWA_SYSTEMBACKDROP_TYPE, &backdrop,
                                     sizeof backdrop)))
        return;
    MARGINS whole = {-1, -1, -1, -1};
    if (FAILED(DwmExtendFrameIntoClientArea(hwnd_, &whole))) return;
    glass_ = true;
    EnumChildWindows(hwnd_, glass_control, reinterpret_cast<LPARAM>(body_));
}

// The window and the scrolling body: their background, and the hairlines
// under the reading and above the buttons.
void SettingsWindow::paint(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);
    HDC target = dc;
    HPAINTBUFFER buffer = nullptr;
    if (glass_) {
        buffer = BeginBufferedPaint(dc, &ps.rcPaint, BPBF_TOPDOWNDIB, nullptr, &target);
        if (!buffer) {
            EndPaint(hwnd, &ps);
            return;
        }
        FillRect(target, &ps.rcPaint, background_);  // white, taken out below
    }
    if (hwnd == hwnd_) {
        RECT client;
        GetClientRect(hwnd_, &client);
        HBRUSH line = CreateSolidBrush(RGB(225, 225, 230));
        RECT top = {0, header_height_ - 1, client.right, header_height_};
        int bottom_y = std::max((int)client.bottom - footer_height_, header_height_);
        RECT bottom = {0, bottom_y, client.right, bottom_y + 1};
        FillRect(target, &top, line);
        FillRect(target, &bottom, line);
        DeleteObject(line);
    }
    if (buffer) {
        white_to_alpha(buffer);
        EndBufferedPaint(buffer, TRUE);
    }
    EndPaint(hwnd, &ps);
}

void SettingsWindow::make_fonts() {
    destroy_fonts();
    NONCLIENTMETRICSW metrics = {};
    metrics.cbSize = sizeof metrics;
    SystemParametersInfoForDpi(SPI_GETNONCLIENTMETRICS, sizeof metrics, &metrics, 0, dpi_);
    LOGFONTW base = metrics.lfMessageFont;
    font_ = CreateFontIndirectW(&base);
    LOGFONTW small = base;
    small.lfHeight = (LONG)std::lround(base.lfHeight * 0.9);
    small_ = CreateFontIndirectW(&small);
    LOGFONTW heading = base;
    heading.lfWeight = FW_SEMIBOLD;
    heading.lfHeight = (LONG)std::lround(base.lfHeight * 1.15);
    heading_ = CreateFontIndirectW(&heading);
    LOGFONTW big = base;
    big.lfWeight = FW_SEMIBOLD;
    big.lfHeight = (LONG)std::lround(base.lfHeight * 2.2);
    big_ = CreateFontIndirectW(&big);
    if (!background_) background_ = CreateSolidBrush(RGB(255, 255, 255));
}

void SettingsWindow::destroy_fonts() {
    for (HFONT* f : {&font_, &small_, &heading_, &big_}) {
        if (*f) DeleteObject(*f);
        *f = nullptr;
    }
}

HWND SettingsWindow::label(HWND parent, const wchar_t* text, HFONT font) {
    HWND h = CreateWindowExW(0, L"STATIC", text, WS_CHILD | WS_VISIBLE | SS_LEFT | SS_NOPREFIX, 0,
                             0, 10, 10, parent, nullptr, nullptr, nullptr);
    SendMessageW(h, WM_SETFONT, (WPARAM)font, TRUE);
    return h;
}

void SettingsWindow::create_controls() {
    for (int i = 0; i < CHECK_COUNT; ++i) {
        checks_[i] = CreateWindowExW(0, L"BUTTON", CHECKS[i].title,
                                     WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX, 0, 0, 10,
                                     10, body_, (HMENU)(INT_PTR)(IDC_CHECK + i), nullptr, nullptr);
        SendMessageW(checks_[i], WM_SETFONT, (WPARAM)font_, TRUE);
        check_help_[i] = label(body_, CHECKS[i].help, small_);
    }
    sensor_heading_ = label(body_, L"Sensor", heading_);
    reading_ = label(hwnd_, L"--", big_);
    reading_caption_ = label(hwnd_, L"", small_);
    sensor_state_ = label(body_, L"", small_);
    problem_ = label(body_, L"", font_);
    preview_ = CreateWindowExW(0, L"BUTTON", L"Preview the effect",
                               WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON, 0, 0, 10, 10,
                               body_, (HMENU)(INT_PTR)IDC_PREVIEW_BUTTON, nullptr, nullptr);
    SendMessageW(preview_, WM_SETFONT, (WPARAM)font_, TRUE);
    for (int s = 0; s < 4; ++s) section_titles_[s] = label(body_, SECTIONS[s].title, heading_);
    section_intro_ = label(body_, SECTIONS[0].intro, small_);
    for (int i = 0; i < SLIDER_COUNT; ++i) {
        slider_titles_[i] = label(body_, SLIDERS[i].title, font_);
        slider_values_[i] = label(body_, L"", font_);
        SetWindowLongW(slider_values_[i], GWL_STYLE,
                       GetWindowLongW(slider_values_[i], GWL_STYLE) | SS_RIGHT);
        sliders_[i] = CreateWindowExW(0, TRACKBAR_CLASSW, L"",
                                      WS_CHILD | WS_VISIBLE | WS_TABSTOP | TBS_HORZ | TBS_NOTICKS, 0,
                                      0, 10, 10, body_, (HMENU)(INT_PTR)(IDC_SLIDER + i), nullptr,
                                      nullptr);
        SendMessageW(sliders_[i], TBM_SETRANGE, TRUE, MAKELPARAM(0, SLIDERS[i].steps));
        // The wheel scrolls the page, not whichever slider it is over.
        SetWindowSubclass(sliders_[i], slider_proc, 1, (DWORD_PTR)this);
        slider_help_[i] = label(body_, SLIDERS[i].help, small_);
    }
    reset_ = CreateWindowExW(0, L"BUTTON", L"Reset", WS_CHILD | WS_VISIBLE | WS_TABSTOP, 0, 0, 10,
                             10, hwnd_, (HMENU)(INT_PTR)IDC_RESET, nullptr, nullptr);
    done_ = CreateWindowExW(0, L"BUTTON", L"Done",
                            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON, 0, 0, 10, 10,
                            hwnd_, (HMENU)(INT_PTR)IDC_DONE, nullptr, nullptr);
    SendMessageW(reset_, WM_SETFONT, (WPARAM)font_, TRUE);
    SendMessageW(done_, WM_SETFONT, (WPARAM)font_, TRUE);
}

// The height a static control needs for its text at a width, wrapping.
static int text_height(HWND control, HFONT font, int width) {
    wchar_t text[1024];
    GetWindowTextW(control, text, 1024);
    HDC dc = GetDC(control);
    HGDIOBJ old = SelectObject(dc, font);
    RECT r = {0, 0, width, 0};
    DrawTextW(dc, text[0] ? text : L"X", -1, &r, DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX);
    SelectObject(dc, old);
    ReleaseDC(control, dc);
    return r.bottom;
}

void SettingsWindow::layout() {
    if (!hwnd_ || !body_ || !reading_) return;  // not while being made
    RECT client;
    GetClientRect(hwnd_, &client);
    int margin = px(24), width = std::max((int)client.right - 2 * margin, px(200));

    // The reading, always at the top.
    int y = px(14);
    int height = text_height(reading_, big_, width);
    SetWindowPos(reading_, nullptr, margin, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE);
    y += height + px(2);
    height = text_height(reading_caption_, small_, width);
    SetWindowPos(reading_caption_, nullptr, margin, y, width, height,
                 SWP_NOZORDER | SWP_NOACTIVATE);
    header_height_ = y + height + px(12);

    // The buttons, always at the bottom.
    int button = px(30), pad = px(14);
    footer_height_ = button + 2 * pad;
    int top = std::max((int)client.bottom - footer_height_, header_height_);
    SetWindowPos(reset_, nullptr, margin, top + pad, px(100), button,
                 SWP_NOZORDER | SWP_NOACTIVATE);
    SetWindowPos(done_, nullptr, margin + width - px(100), top + pad, px(100), button,
                 SWP_NOZORDER | SWP_NOACTIVATE);

    // The rest scrolls between them.
    SetWindowPos(body_, nullptr, 0, header_height_, client.right,
                 std::max(top - header_height_, 0), SWP_NOZORDER | SWP_NOACTIVATE);
    layout_body();
    InvalidateRect(hwnd_, nullptr, TRUE);
}

void SettingsWindow::layout_body() {
    if (!body_ || !checks_[0]) return;
    RECT client;
    GetClientRect(body_, &client);
    int margin = px(24), width = std::max((int)client.right - 2 * margin, px(200));
    // Twice at most: once more when the content got shorter than the scroll.
    for (int pass = 0; pass < 2; ++pass) {
        int y = px(8) - scroll_;
        auto place = [&](HWND h, int height, int x = 0, int w = -1) {
            SetWindowPos(h, nullptr, margin + x, y, w < 0 ? width - x : w, height,
                         SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOREDRAW);
        };
        auto text = [&](HWND h, HFONT f, int gap) {
            int height = text_height(h, f, width);
            place(h, height);
            y += height + gap;
        };

        for (int i = 0; i < CHECK_COUNT; ++i) {
            place(checks_[i], px(24));
            y += px(24);
            text(check_help_[i], small_, px(10));
        }
        y += px(10);
        text(sensor_heading_, heading_, px(6));
        text(sensor_state_, small_, px(6));
        if (IsWindowVisible(problem_)) text(problem_, font_, px(6));
        place(preview_, px(30), 0, px(170));
        y += px(30) + px(18);

        int slider = 0;
        for (int s = 0; s < 4; ++s) {
            text(section_titles_[s], heading_, px(6));
            if (s == 0) text(section_intro_, small_, px(10));
            int end = s + 1 < 4 ? SECTIONS[s + 1].first_slider : SLIDER_COUNT;
            for (; slider < end; ++slider) {
                int row = px(22);
                place(slider_titles_[slider], row, 0, width - px(90));
                place(slider_values_[slider], row, width - px(90), px(90));
                y += row;
                place(sliders_[slider], px(30));
                y += px(30);
                text(slider_help_[slider], small_, px(14));
            }
            y += px(8);
        }
        content_height_ = y + scroll_;
        int most = std::max(0, content_height_ - (int)client.bottom);
        if (scroll_ <= most) break;
        scroll_ = most;
    }

    SCROLLINFO si = {};
    si.cbSize = sizeof si;
    si.fMask = SIF_RANGE | SIF_PAGE | SIF_POS;
    si.nMin = 0;
    si.nMax = std::max(content_height_ - 1, 0);
    si.nPage = (UINT)client.bottom;
    si.nPos = scroll_;
    SetScrollInfo(body_, SB_VERT, &si, TRUE);
    RedrawWindow(body_, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN);
}

// Scrolling moves what is already drawn and paints only the strip that comes
// into view, so nothing blanks out while the page moves.
void SettingsWindow::scroll_to(int offset) {
    if (!body_) return;
    RECT client;
    GetClientRect(body_, &client);
    int most = std::max(0, content_height_ - (int)client.bottom);
    offset = std::min(std::max(offset, 0), most);
    if (offset == scroll_) return;
    int delta = scroll_ - offset;
    scroll_ = offset;
    SCROLLINFO si = {};
    si.cbSize = sizeof si;
    si.fMask = SIF_POS;
    si.nPos = scroll_;
    SetScrollInfo(body_, SB_VERT, &si, TRUE);
    ScrollWindowEx(body_, 0, delta, nullptr, nullptr, nullptr, nullptr,
                   SW_SCROLLCHILDREN | SW_INVALIDATE | SW_ERASE);
    UpdateWindow(body_);
}

double SettingsWindow::slider_value(int i) const {
    const SliderSpec& spec = SLIDERS[i];
    int position = (int)SendMessageW(sliders_[i], TBM_GETPOS, 0, 0);
    return spec.minimum + (spec.maximum - spec.minimum) * position / spec.steps;
}

void SettingsWindow::set_value_text(int i) {
    const SliderSpec& spec = SLIDERS[i];
    double shown = slider_value(i) * spec.display_scale;
    wchar_t text[64];
    swprintf(text, 64, L"%.*f%ls", spec.decimals, shown, spec.suffix);
    SetWindowTextW(slider_values_[i], text);
}

void SettingsWindow::load() {
    if (!hwnd_) return;
    loading_ = true;
    Settings s = store_.get();
    for (int i = 0; i < CHECK_COUNT; ++i)
        SendMessageW(checks_[i], BM_SETCHECK, CHECKS[i].get(s) ? BST_CHECKED : BST_UNCHECKED, 0);
    for (int i = 0; i < SLIDER_COUNT; ++i) {
        const SliderSpec& spec = SLIDERS[i];
        double value = spec.get(s);
        int position = (int)std::lround((value - spec.minimum) / (spec.maximum - spec.minimum) *
                                        spec.steps);
        SendMessageW(sliders_[i], TBM_SETPOS, TRUE, std::min(std::max(position, 0), spec.steps));
        set_value_text(i);
    }
    loading_ = false;
}

void SettingsWindow::refresh_status() {
    if (!hwnd_ || !IsWindowVisible(hwnd_)) return;
    std::string problem = engine_.problem();
    bool had_problem = IsWindowVisible(problem_) != FALSE;
    SetWindowTextW(problem_, widen(problem).c_str());
    ShowWindow(problem_, problem.empty() ? SW_HIDE : SW_SHOW);

    SensorStatus status = engine_.sensor().status();
    wchar_t text[256];
    if (status.running)
        swprintf(text, 256, L"Reading the lid from the %ls, %.0f times a second.",
                 widen(status.name).c_str(), status.rate);
    else
        swprintf(text, 256, L"%ls",
                 status.problem.empty() ? L"Looking for a lid sensor." : widen(status.problem).c_str());
    SetWindowTextW(sensor_state_, text);

    AngleSample sample;
    if (!engine_.sensor().latest(sample, 1.0)) {
        SetWindowTextW(reading_, L"--");
        SetWindowTextW(reading_caption_, L"Waiting for the lid sensor");
    } else {
        swprintf(text, 256, L"%.0f°", std::round(sample.travel));
        SetWindowTextW(reading_, text);
        swprintf(text, 256, L"%ls · lid at %.0f° · confidence %.0f%%",
                 sample.at_rest ? L"Resting" : sample.travel > 0 ? L"Closing" : L"Opening",
                 sample.absolute_angle, sample.confidence * 100);
        SetWindowTextW(reading_caption_, text);
    }
    if (had_problem != !problem.empty()) layout_body();
}

LRESULT CALLBACK SettingsWindow::slider_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam,
                                            UINT_PTR, DWORD_PTR data) {
    if (msg == WM_MOUSEWHEEL) {
        auto* self = reinterpret_cast<SettingsWindow*>(data);
        return SendMessageW(self->body_, msg, wparam, lparam);
    }
    return DefSubclassProc(hwnd, msg, wparam, lparam);
}

LRESULT CALLBACK SettingsWindow::proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    SettingsWindow* self;
    if (msg == WM_NCCREATE) {
        self = static_cast<SettingsWindow*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        if (!self->hwnd_) self->hwnd_ = hwnd;  // the body comes second
    } else {
        self = reinterpret_cast<SettingsWindow*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    }
    if (self) return self->handle(hwnd, msg, wparam, lparam);
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

LRESULT SettingsWindow::handle(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    // The main window and its scrolling body share this; the messages from
    // controls arrive at whichever of the two holds them.
    bool is_main = hwnd == hwnd_;
    switch (msg) {
        case WM_CLOSE:
            if (!is_main) break;
            ShowWindow(hwnd_, SW_HIDE);  // kept for next time, like the Qt window
            return 0;
        case WM_SIZE:
            if (is_main) layout();  // the body is sized by layout() itself
            return 0;
        case WM_ERASEBKGND:
            if (glass_) return 1;  // painted on white in WM_PAINT, then made see-through
            break;
        case WM_PRINTCLIENT:
            // A themed button asking for what is behind its rounded corners.
            if (glass_) {
                RECT client;
                GetClientRect(hwnd, &client);
                FillRect(reinterpret_cast<HDC>(wparam), &client, background_);
                return 0;
            }
            break;
        case WM_PAINT:
            paint(hwnd);
            return 0;
        case WM_DPICHANGED: {
            if (!is_main) break;
            dpi_ = HIWORD(wparam);
            RECT* suggested = reinterpret_cast<RECT*>(lparam);
            make_fonts();
            auto refont = [&](HWND h, HFONT f) { SendMessageW(h, WM_SETFONT, (WPARAM)f, TRUE); };
            for (int i = 0; i < CHECK_COUNT; ++i) {
                refont(checks_[i], font_);
                refont(check_help_[i], small_);
            }
            refont(sensor_heading_, heading_);
            refont(reading_, big_);
            refont(reading_caption_, small_);
            refont(sensor_state_, small_);
            refont(problem_, font_);
            refont(preview_, font_);
            for (HWND h : section_titles_) refont(h, heading_);
            refont(section_intro_, small_);
            for (int i = 0; i < SLIDER_COUNT; ++i) {
                refont(slider_titles_[i], font_);
                refont(slider_values_[i], font_);
                refont(slider_help_[i], small_);
            }
            refont(reset_, font_);
            refont(done_, font_);
            SetWindowPos(hwnd_, nullptr, suggested->left, suggested->top,
                         suggested->right - suggested->left, suggested->bottom - suggested->top,
                         SWP_NOZORDER | SWP_NOACTIVATE);
            layout();
            return 0;
        }
        case WM_GETMINMAXINFO: {
            if (!is_main) break;
            auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
            info->ptMinTrackSize.x = px(420);
            info->ptMinTrackSize.y = px(300);
            return 0;
        }
        case WM_MOUSEWHEEL:
            scroll_to(scroll_ - GET_WHEEL_DELTA_WPARAM(wparam) * px(60) / WHEEL_DELTA);
            return 0;
        case WM_VSCROLL: {
            SCROLLINFO si = {};
            si.cbSize = sizeof si;
            si.fMask = SIF_ALL;
            GetScrollInfo(body_, SB_VERT, &si);
            int pos = si.nPos;
            switch (LOWORD(wparam)) {
                case SB_LINEUP: pos -= px(30); break;
                case SB_LINEDOWN: pos += px(30); break;
                case SB_PAGEUP: pos -= (int)si.nPage; break;
                case SB_PAGEDOWN: pos += (int)si.nPage; break;
                case SB_THUMBTRACK:
                case SB_THUMBPOSITION: pos = si.nTrackPos; break;
                case SB_TOP: pos = 0; break;
                case SB_BOTTOM: pos = content_height_; break;
            }
            scroll_to(pos);
            return 0;
        }
        case WM_HSCROLL: {
            HWND from = reinterpret_cast<HWND>(lparam);
            for (int i = 0; i < SLIDER_COUNT; ++i) {
                if (from != sliders_[i]) continue;
                set_value_text(i);
                if (!loading_) {
                    double value = slider_value(i);
                    const SliderSpec& spec = SLIDERS[i];
                    store_.update([&](Settings& s) { spec.set(s, value); });
                }
            }
            return 0;
        }
        case WM_COMMAND: {
            int id = LOWORD(wparam);
            if (id >= IDC_CHECK && id < IDC_CHECK + CHECK_COUNT && !loading_) {
                int i = id - IDC_CHECK;
                bool on = SendMessageW(checks_[i], BM_GETCHECK, 0, 0) == BST_CHECKED;
                store_.update([&](Settings& s) { CHECKS[i].set(s, on); });
            } else if (id == IDC_PREVIEW_BUTTON) {
                engine_.run_preview();
            } else if (id == IDC_RESET) {
                store_.reset();
                load();
            } else if (id == IDC_DONE) {
                ShowWindow(hwnd_, SW_HIDE);
            }
            return 0;
        }
        case WM_CTLCOLORSTATIC: {
            HDC dc = reinterpret_cast<HDC>(wparam);
            HWND from = reinterpret_cast<HWND>(lparam);
            SetBkColor(dc, RGB(255, 255, 255));
            bool quiet = from == sensor_state_ || from == reading_caption_ || from == section_intro_;
            for (HWND h : check_help_) quiet = quiet || from == h;
            for (HWND h : slider_help_) quiet = quiet || from == h;
            SetTextColor(dc, from == problem_ ? RGB(180, 40, 30)
                             : quiet          ? RGB(105, 105, 110)
                                              : RGB(20, 20, 24));
            return reinterpret_cast<LRESULT>(background_);
        }
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

// ---------------------------------------------------------------------------
// The tray icon

static const wchar_t* TRAY_CLASS = L"WinDuo2Tray";

Tray::Tray(SettingsStore& store, Engine& engine) : store_(store), engine_(engine) {}

Tray::~Tray() {
    if (added_) {
        NOTIFYICONDATAW data = {};
        data.cbSize = sizeof data;
        data.hWnd = hwnd_;
        data.uID = 1;
        Shell_NotifyIconW(NIM_DELETE, &data);
    }
    delete settings_;
    if (hwnd_) DestroyWindow(hwnd_);
}

HWND Tray::settings_hwnd() const { return settings_ ? settings_->hwnd() : nullptr; }

bool Tray::create() {
    WNDCLASSW wc = {};
    wc.lpfnWndProc = proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = TRAY_CLASS;
    RegisterClassW(&wc);
    // A hidden top-level window rather than a message-only one: only top-level
    // windows hear TaskbarCreated and WM_DISPLAYCHANGE.
    hwnd_ = CreateWindowExW(WS_EX_TOOLWINDOW, TRAY_CLASS, L"WinDuo2", WS_POPUP, 0, 0, 0, 0,
                            nullptr, nullptr, wc.hInstance, this);
    if (!hwnd_) return false;
    taskbar_created_ = RegisterWindowMessageW(L"TaskbarCreated");
    icon_ = (HICON)LoadImageW(wc.hInstance, MAKEINTRESOURCEW(IDI_APP), IMAGE_ICON,
                              GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), 0);
    return true;
}

void Tray::add_icon() {
    NOTIFYICONDATAW data = {};
    data.cbSize = sizeof data;
    data.hWnd = hwnd_;
    data.uID = 1;
    data.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP | NIF_SHOWTIP;
    data.uCallbackMessage = WM_TRAY;
    data.hIcon = icon_;
    wcsncpy_s(data.szTip, L"WinDuo2", _TRUNCATE);
    added_ = Shell_NotifyIconW(NIM_ADD, &data) != FALSE;
    if (added_) {
        data.uVersion = NOTIFYICON_VERSION_4;
        Shell_NotifyIconW(NIM_SETVERSION, &data);
    } else {
        LOGW("tray", "the notification area refused the icon; trying again when the taskbar is back");
    }
    tooltip_.clear();
}

void Tray::show(bool introduce) {
    add_icon();
    SetTimer(hwnd_, TIMER_REFRESH, 400, nullptr);
    refresh();
    if (introduce && !store_.get().has_seen_intro) {
        store_.update([](Settings& s) { s.has_seen_intro = true; });
        NOTIFYICONDATAW data = {};
        data.cbSize = sizeof data;
        data.hWnd = hwnd_;
        data.uID = 1;
        data.uFlags = NIF_INFO;
        wcsncpy_s(data.szInfoTitle, L"WinDuo2 is ready", _TRUNCATE);
        wcsncpy_s(data.szInfo,
                  L"Close the lid and the screen will lean away. WinDuo2 lives in the "
                  L"notification area; right-click it for settings.",
                  _TRUNCATE);
        data.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON;
        data.hBalloonIcon = icon_;
        Shell_NotifyIconW(NIM_MODIFY, &data);
        SetTimer(hwnd_, TIMER_INTRO, 700, nullptr);
    }
}

void Tray::open_settings() {
    if (!settings_) settings_ = new SettingsWindow(store_, engine_);
    settings_->show();
}

std::wstring Tray::status_text() const {
    Settings s = store_.get();
    std::string problem = engine_.problem();
    if (!s.enabled) return L"Effect off";
    if (!problem.empty()) return widen(problem);
    if (engine_.is_effect_running()) return L"Running";
    AngleSample sample;
    if (!const_cast<Engine&>(engine_).sensor().latest(sample, 1.0)) return L"Waiting for the lid sensor";
    if (sample.at_rest) return L"Resting, ready";
    wchar_t text[64];
    swprintf(text, 64, L"%.0f° from resting", std::max(sample.travel, 0.0));
    return text;
}

void Tray::refresh() {
    std::wstring tip = store_.get().show_reading ? L"WinDuo2 — " + status_text() : L"WinDuo2";
    if (tip.size() > 127) tip.resize(127);
    if (added_ && tip != tooltip_) {
        NOTIFYICONDATAW data = {};
        data.cbSize = sizeof data;
        data.hWnd = hwnd_;
        data.uID = 1;
        data.uFlags = NIF_TIP | NIF_SHOWTIP;
        wcsncpy_s(data.szTip, tip.c_str(), _TRUNCATE);
        Shell_NotifyIconW(NIM_MODIFY, &data);
        tooltip_ = tip;
    }
    if (settings_) settings_->refresh_status();
}

void Tray::show_menu() {
    HMENU menu = CreatePopupMenu();
    Settings s = store_.get();
    AppendMenuW(menu, MF_STRING | (s.enabled ? MF_CHECKED : 0), ID_EFFECT, L"Run the effect");
    AppendMenuW(menu, MF_STRING, ID_PREVIEW, L"Preview once");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, ID_SETTINGS, L"Settings…");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, status_text().c_str());
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, ID_QUIT, L"Quit WinDuo2");
    POINT at;
    GetCursorPos(&at);
    SetForegroundWindow(hwnd_);  // so the menu closes when clicking elsewhere
    UINT chosen = (UINT)TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
                                       at.x, at.y, 0, hwnd_, nullptr);
    DestroyMenu(menu);
    switch (chosen) {
        case ID_EFFECT:
            store_.update([](Settings& st) { st.enabled = !st.enabled; });
            break;
        case ID_PREVIEW:
            engine_.run_preview();
            break;
        case ID_SETTINGS:
            open_settings();
            break;
        case ID_QUIT:
            PostQuitMessage(0);
            break;
    }
}

LRESULT CALLBACK Tray::proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    Tray* self;
    if (msg == WM_NCCREATE) {
        self = static_cast<Tray*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(self));
        self->hwnd_ = hwnd;
    } else {
        self = reinterpret_cast<Tray*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    }
    if (self) return self->handle(msg, wparam, lparam);
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

LRESULT Tray::handle(UINT msg, WPARAM wparam, LPARAM lparam) {
    if (msg == taskbar_created_ && taskbar_created_) {
        // Explorer restarted, or was not up yet when this started: add again.
        add_icon();
        return 0;
    }
    switch (msg) {
        case WM_TRAY:
            switch (LOWORD(lparam)) {
                case NIN_SELECT:
                case NIN_KEYSELECT:
                case WM_LBUTTONDBLCLK:
                    open_settings();
                    break;
                case WM_CONTEXTMENU:
                    show_menu();
                    break;
            }
            return 0;
        case WM_TIMER:
            if (wparam == TIMER_REFRESH) {
                if (!added_) add_icon();
                refresh();
            } else if (wparam == TIMER_INTRO) {
                KillTimer(hwnd_, TIMER_INTRO);
                open_settings();
            }
            return 0;
        case WM_DISPLAYCHANGE:
            engine_.display_changed();
            return 0;
        case WM_SETTINGCHANGE:
            if (wparam == SPI_SETWORKAREA) engine_.display_changed();
            return 0;
    }
    return DefWindowProcW(hwnd_, msg, wparam, lparam);
}

}  // namespace wd
