# Windows Audio Device Switcher

A standalone Windows desktop app for switching default playback and recording audio devices.

**Motivation**

I frequently switch between multiple audio devices (headsets, earphones, speakers, etc.) and found it inconvenient to change the default devices in Discord and other applications every time. This tool was created to make switching default playback and recording devices quick and seamless.

## Features

- Playback and Recording tabs
- Set a device as:
  - Default
  - Communications
  - Both
- Saved/preferred device system
- First launch automatically shows all devices when no saved devices exist
- `장비 등록` / `장비 스위칭` mode per tab
- In `장비 등록` mode:
  - The right column shows `Saved`
  - Double-click toggles whether a device is saved
  - Saved devices are shown as `✓ Saved`
- In `장비 스위칭` mode:
  - The right column shows the current role
  - Double-click sets `Both`
- System tray menu:
  - `Open Controller`
  - `Both`
  - `Communication`
  - `Default`
  - `Exit`
- Closing the controller window hides it instead of exiting the app
- Tray `Exit` fully quits the app
- Optional Windows startup registration
- Korean device names and Korean UI status messages
- Windows 10 / 11 support without administrator rights where possible

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- Audio devices supported by Windows Core Audio

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
py main.py
```

## Startup Behavior

The app has a `Windows 시작 시 자동 실행` checkbox.

When enabled, the app registers itself in:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

Startup launches the app with:

```text
--minimized
```

That means:

- Normal launch: controller window opens and tray icon stays active
- Windows startup launch: controller window stays hidden and only the tray icon appears
- Window close button: hides the controller
- Tray `Open Controller`: shows the controller again
- Tray `Exit`: fully exits

## SoundVolumeView Fallback

The primary switching path uses `pycaw` and `comtypes`.

`SoundVolumeView.exe` is optional. The app can run without it if direct COM switching works on your system.

To enable fallback switching:

1. Download `SoundVolumeView.exe` from NirSoft.
2. Place `SoundVolumeView.exe` next to `main.py` or the built `.exe`.
3. Run the app normally.

`SoundVolumeView.exe` is ignored by git because it is a third-party binary.

## Build EXE

Install PyInstaller:

```powershell
pip install pyinstaller
```

Build without SoundVolumeView:

```powershell
pyinstaller --noconfirm --windowed --name "WindowsAudioDeviceSwitcher" main.py
```

Optional build with SoundVolumeView bundled:

```powershell
pyinstaller --noconfirm --windowed --name "WindowsAudioDeviceSwitcher" --add-binary "SoundVolumeView.exe;." main.py
```

Only use the second command if `SoundVolumeView.exe` exists in the project folder.

## Files

- `main.py` - application entry point
- `ui.py` - CustomTkinter/Tkinter UI and tray menu
- `device_manager.py` - Windows audio device enumeration and switching
- `config.py` - config, preferred-device persistence, and startup registration
- `requirements.txt` - Python dependencies

## Local Files

The following files are local runtime/build artifacts and are ignored by git:

- `.venv/`
- `config.json`
- `audio_switcher.log`
- `SoundVolumeView.exe`
- `build/`
- `dist/`
- `*.spec`

## License

MIT License. See [LICENSE](LICENSE).
