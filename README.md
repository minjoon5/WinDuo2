# WinDuo2

Close your laptop lid and the screen leans away, blurring and dimming as it goes, the way an iPhone Duo does as it folds shut. Open it again and the screen stands back up out of the blur.

WinDuo2 reads the laptop's own sensors: a hinge angle sensor where Windows exposes one, otherwise the accelerometer in the lid that convertibles such as the Galaxy Book Pro 360 use for screen rotation. No camera, no calibration.

Windows 10 (2004+) and 11, Linux on Hyprland · Free for noncommercial use, [PolyForm Noncommercial 1.0.0](LICENSE.md)

## Install

**Just want to use it:** download `WinDuo2.exe` from the [latest release](https://github.com/minjoon5/WinDuo2/releases/latest). One file, nothing else to install, no Python. Windows will warn about an unrecognised app because the build is unsigned — choose *More info*, then *Run anyway*.

**From source:**

```sh
pip install git+https://github.com/minjoon5/WinDuo2
python -m winduo2
```

WinDuo2 sits in the notification area. Right-click it for settings. Tick *Start with Windows* there to have it run every time you sign in; it then appears in Task Manager under *Startup apps*.

To check that your laptop has a sensor WinDuo2 can read, run `python -m winduo2 --sensors` and move the lid: it prints the angle it sees for twenty seconds. `python -m winduo2 --preview` plays the effect once on whatever is on screen.

## How it follows the lid

The effect is continuous, not a one-shot animation. It fades in once you have closed about 10°, grows as you keep closing, reaches full strength at about 85°, and holds there for the rest of the way down. Open back up and it reverses. Both thresholds are adjustable.

Opening plays the same effect backwards. Lift the lid from shut, or from anywhere below where your last close started, and the screen shows the effect at the strength that angle had on the way down, then straightens up and clears as the lid rises back to where it was open. A lid already open that you tilt further back leaves the screen alone.

The picture behaves like a sheet hinged at the bottom of the screen that leans back as the lid closes, so its top edge comes down and narrows. By default it leans back at 0.8° per degree of closing, and the tilt levels off at about 55° past the trigger instead of stretching the picture into a smear as the lid nears flat. *Lean back* and *Tilt limit* in the settings adjust both.

The angle comes from the first of these the laptop offers:

1. **A hinge angle sensor** (`Windows.Devices.Sensors.HingeAngleSensor`), which reports the angle between lid and base directly.
2. **The lid accelerometer** (`Windows.Devices.Sensors.Accelerometer`). Gravity seen in the screen's own axes gives the lid's tilt, which is the lid angle whenever the base is level.

Whenever the lid holds still, that position becomes zero, and the effect runs on movement away from it. A lid that stops partway becomes the new zero, so the effect eases out on its own. It also means the accelerometer works with the laptop on a lap or a tilted desk: the base does not have to be level, only still.

## Known limits

- **It needs a sensor in the lid.** Convertibles have one for screen rotation; most ordinary clamshell laptops have none, and then WinDuo2 says so in the tray. Convertibles that carry a second accelerometer in the base have both read, and WinDuo2 follows whichever one turns with the lid, so the first close after starting may only partly show the effect. If `--sensors` still shows an angle that does not change as the lid moves, the only accelerometer Windows exposes is in the base, which cannot see the lid.
- **With only the accelerometer, tipping the whole laptop back can trigger it.** It cannot tell the lid turning from the machine turning. A laptop being carried reads as far from 1 g and is ignored, but a slow tilt is not. A hinge angle sensor has neither problem.
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
- **Hyprland only.** The overlay is floated, pinned and sized with `hyprctl dispatch`, which works under both Hyprland config formats. Other compositors are not supported.
- The lid is read from the IIO accelerometer under `/sys/bus/iio/devices`, preferring the one the kernel labels `accel-display`, and the lid switch from `/proc/acpi/button/lid`.

The Linux port has been unit tested but not yet run on real Omarchy hardware. Reports are welcome.

## Contributing

`pip install -e ".[dev]"` then `pytest`. The tests need no sensor or lid.
