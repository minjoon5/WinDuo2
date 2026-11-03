// The tray icon and the settings window. Ported from the Python app's
// winduo2/ui/tray.py and winduo2/ui/settings.py (in the git history).

#pragma once

#include "engine.h"
#include "platform.h"

namespace wd {

class SettingsWindow;

class Tray {
public:
    Tray(SettingsStore& store, Engine& engine);
    ~Tray();
    bool create();
    void show(bool introduce);
    void open_settings();
    HWND settings_hwnd() const;

private:
    static LRESULT CALLBACK proc(HWND, UINT, WPARAM, LPARAM);
    LRESULT handle(UINT, WPARAM, LPARAM);
    void add_icon();
    void refresh();
    void show_menu();
    std::wstring status_text() const;

    SettingsStore& store_;
    Engine& engine_;
    HWND hwnd_ = nullptr;
    HICON icon_ = nullptr;
    UINT taskbar_created_ = 0;
    bool added_ = false;
    std::wstring tooltip_;
    SettingsWindow* settings_ = nullptr;
};

}  // namespace wd
