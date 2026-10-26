# WinDuo

Close your laptop lid and the screen leans away, blurring and dimming as it goes, the way an iPhone Duo does as it folds shut.

[Mac Duo](https://github.com/sumimakito/Mac-Duo) reads the MacBook's lid angle sensor. WinDuo reads the laptop's own sensors: a hinge angle sensor where Windows exposes one, otherwise the accelerometer in the lid that convertibles such as the Galaxy Book Pro 360 use for screen rotation. No camera, no calibration.

Windows 10 (2004+) and 11, Windhawk, Linux on Hyprland · Free for noncommercial use, [PolyForm Noncommercial 1.0.0](LICENSE)

## Install

**Just want to use it:** download `WinDuo.exe` from the [latest release](https://github.com/BaselAshraf81/winduo/releases/latest). One file, nothing else to install, no Python. Windows will warn about an unrecognised app because the build is unsigned — choose *More info*, then *Run anyway*.

**From source:**

```sh
pip install git+https://github.com/BaselAshraf81/winduo
python -m winduo
```

WinDuo sits in the notification area. Right-click it for settings.

To check that your laptop has a sensor WinDuo can read, run `python -m winduo --sensors` and move the lid: it prints the angle it sees for twenty seconds. `python -m winduo --preview` plays the effect once on whatever is on screen.

## How it follows the lid

The effect is continuous, not a one-shot animation. It fades in once you have closed about 10°, grows as you keep closing, reaches full strength at about 85°, and holds there for the rest of the way down. Open back up and it reverses. Both thresholds are adjustable.

The picture behaves like a sheet hinged at the bottom of the screen that stays where it is in the room while the glass turns under it, and tilts back as the lid closes. By default it leans back at 0.5° per degree of closing, and the tilt levels off at about 55° past the trigger instead of stretching the picture into a smear as the lid nears flat. *Lean back* and *Tilt limit* in the settings adjust both.

The angle comes from the first of these the laptop offers:

1. **A hinge angle sensor** (`Windows.Devices.Sensors.HingeAngleSensor`), which reports the angle between lid and base directly.
2. **The lid accelerometer** (`Windows.Devices.Sensors.Accelerometer`). Gravity seen in the screen's own axes gives the lid's tilt, which is the lid angle whenever the base is level.

Whenever the lid holds still, that position becomes zero, and the effect runs on movement away from it. A lid that stops partway becomes the new zero, so the effect eases out on its own. It also means the accelerometer works with the laptop on a lap or a tilted desk: the base does not have to be level, only still.

## Known limits

- **It needs a sensor in the lid.** Convertibles have one for screen rotation; most ordinary clamshell laptops have none, and then WinDuo says so in the tray. If `--sensors` shows an angle that does not change as the lid moves, the accelerometer Windows exposes is in the base, which cannot see the lid.
- **With only the accelerometer, tipping the whole laptop back can trigger it.** It cannot tell the lid turning from the machine turning. A laptop being carried reads as far from 1 g and is ignored, but a slow tilt is not. A hinge angle sensor has neither problem.
- **Folded past flat, it stays off,** so unfolding from tent or tablet mode does not count as closing.
- **Windows turns the screen off when the lid shuts,** so you only see the effect during the closing movement.
- Primary display only.

## Windhawk

[windhawk/winduo.wh.cpp](windhawk/winduo.wh.cpp) is a native C++ build of the same effect packaged as a [Windhawk](https://windhawk.net) mod. It runs in its own process, injects into nothing, and needs no Python. It still reads the lid from the webcam rather than the sensors.

## Linux (Omarchy / Hyprland)

```sh
curl -fsSL https://raw.githubusercontent.com/BaselAshraf81/winduo/main/packaging/omarchy/install.sh | bash
```

This installs the Arch packages it needs, creates a virtualenv under `~/.local/share/winduo`, and starts WinDuo with Hyprland. `packaging/omarchy/uninstall.sh` undoes it. Differences from Windows:

- **The picture is held, not live.** Wayland has no way for the overlay to keep itself out of a screen capture, so WinDuo captures one frame as the lid starts to move (with `grim`) and animates that.
- **Hyprland only.** The overlay is floated, pinned and sized with `hyprctl dispatch`, which works under both Hyprland config formats. Other compositors are not supported.
- The lid is read from the IIO accelerometer under `/sys/bus/iio/devices`, preferring the one the kernel labels `accel-display`, and the lid switch from `/proc/acpi/button/lid`.

The Linux port has been unit tested but not yet run on real Omarchy hardware. Reports are welcome.

## Contributing

`pip install -e ".[dev]"` then `pytest`. The tests need no sensor or lid.
