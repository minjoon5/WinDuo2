# WinDuo2

Close your laptop lid and the screen leans away, blurring and dimming as it goes, the way an iPhone Duo does as it folds shut. Open it again and the screen stands back up out of the blur.

WinDuo2 reads the laptop's own hinge angle sensor: the one Windows exposes as standard, or the Intel Integrated Sensor Hub's hinge sensor on convertibles such as the Galaxy Book 360 series. No camera, no calibration.

Windows 10 (2004+) and 11, Linux on Hyprland · Free for noncommercial use, [PolyForm Noncommercial 1.0.0](LICENSE.md)

## Install

**Just want to use it:** download `WinDuo2.exe` from the [latest release](https://github.com/minjoon5/WinDuo2/releases/latest). One file, nothing else to install, no Python. Windows will warn about an unrecognised app because the build is unsigned — choose *More info*, then *Run anyway*.

**From source:**

```sh
pip install git+https://github.com/minjoon5/WinDuo2
python -m winduo2
```

WinDuo2 sits in the notification area. Right-click it for settings. *Start with Windows* is on from the first run, so it runs every time you sign in; untick it in the settings to stop that. It is registered as a *WinDuo2* task in Task Scheduler that runs as soon as you sign in, rather than as a Task Manager startup app, which Windows holds back for a while after sign-in. The settings window opens by itself only the very first time WinDuo2 runs, never at sign-in.

To check that your laptop has a sensor WinDuo2 can read, run `python -m winduo2 --sensors` and move the lid: it prints the angle it sees for twenty seconds. `python -m winduo2 --preview` plays the effect once on whatever is on screen.

## How it follows the lid

The effect is continuous, not a one-shot animation. It fades in once you have closed about 10°, grows as you keep closing, reaches full strength at about 85°, and holds there for the rest of the way down. Open back up and it reverses. Both thresholds are adjustable.

Opening has an effect of its own, the mirror of a close. Lift the lid from shut, or tilt an open screen further back, and once it has opened about 10° the picture starts to spread: its top edge widens past both sides of the screen and runs up past the top, with the bottom edge held on the hinge, growing as you keep opening and reaching full strength at about 85°. When the lid comes to rest it eases back into place.

The picture behaves like a sheet hinged at the bottom of the screen that leans back as the lid closes, so its top edge comes down and narrows. By default it leans back at 0.8° per degree of closing, and the tilt levels off at about 55° past the trigger instead of stretching the picture into a smear as the lid nears flat. *Lean back* and *Tilt limit* in the settings adjust both.

The angle comes from the first of these the laptop offers:

1. **Windows' hinge angle sensor** (`Windows.Devices.Sensors.HingeAngleSensor`), which reports the angle between lid and base directly.
2. **The Intel ISH hinge sensor**, a custom sensor (`Windows.Devices.Sensors.Custom`, HID usage 0x020B) that Galaxy Book 360 models expose instead. The sensor hub's firmware works the angle out from the accelerometers in both the lid and the base, so it is the true hinge angle with the laptop on a lap or a tilted desk.

Whenever the lid holds still, that position becomes zero, and the effect runs on movement away from it. A lid that stops partway becomes the new zero, so the effect eases out on its own.

## Known limits

- **It needs a hinge angle sensor.** Laptops without one, which includes most ordinary clamshells, are not supported, and WinDuo2 says so in the tray. The lid accelerometer alone is no longer used on Windows.
- **Folded past flat, it stays off,** so unfolding from tent or tablet mode does not count as closing.
- **Start, search and the notification flyouts close when the effect starts.** Windows keeps them in a layer above every window an ordinary app can make, so they cannot be blurred in place; closing them is what the lid closing would do anyway. The pointer is hidden for the length of the effect and drawn into the picture instead, so it blurs with everything else. An app that draws a cursor of its own, as some games do, keeps it sharp.
- **Windows turns the screen off when the lid shuts,** so you only see the effect during the closing movement.
- Primary display only.

## Linux (Omarchy / Hyprland)

```sh
curl -fsSL https://raw.githubusercontent.com/minjoon5/WinDuo2/main/packaging/omarchy/install.sh | bash
```

This installs the Arch packages it needs, creates a virtualenv under `~/.local/share/winduo2`, and starts WinDuo2 with Hyprland. `packaging/omarchy/uninstall.sh` undoes it. Differences from Windows:

- **The picture is held, not live.** Wayland has no way for the overlay to keep itself out of a screen capture, so WinDuo2 captures one frame as the lid starts to move (with `grim`) and animates that.
- **The lid is read from an accelerometer, not a hinge sensor.** Gravity seen in the screen's own axes gives the lid's tilt, which is the lid angle whenever the base is level, and the effect only acts on change, so a lap or a tilted desk works as long as the base is still. Tipping the whole laptop back can trigger it, though: it cannot tell the lid turning from the machine turning.
- **Hyprland only.** The overlay is floated, pinned and sized with `hyprctl dispatch`, which works under both Hyprland config formats. Other compositors are not supported.
- The lid is read from the IIO accelerometer under `/sys/bus/iio/devices`, preferring the one the kernel labels `accel-display`, and the lid switch from `/proc/acpi/button/lid`.

The Linux port has been unit tested but not yet run on real Omarchy hardware. Reports are welcome.

## Contributing

`pip install -e ".[dev]"` then `pytest`. The tests need no sensor or lid.
