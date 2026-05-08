# Windows Audio Device Switcher

A standalone Windows desktop app for switching default playback and recording audio devices.

The app supports:

- Playback and Recording tabs
- Set device as Default, Communications, or Both
- Double-click a device to set Both roles
- Saved/preferred device view by default
- Toggle to show all available devices
- Missing saved-device warnings
- System tray quick switching
- Korean device names
- Windows 10 / 11 without administrator rights where possible

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

## SoundVolumeView Fallback

The primary switching path uses `pycaw` and `comtypes`. If direct COM switching fails, the app can fall back to NirSoft SoundVolumeView.

To enable the fallback:

1. Download `SoundVolumeView.exe` from NirSoft.
2. Place `SoundVolumeView.exe` next to `main.py`.
3. Run the app normally.

`SoundVolumeView.exe` is intentionally ignored by git because it is a third-party binary.

## Build EXE

Install PyInstaller:

```powershell
pip install pyinstaller
```

Build:

```powershell
pyinstaller --noconfirm --windowed --name "WindowsAudioDeviceSwitcher" --add-binary "SoundVolumeView.exe;." main.py
```

If you do not bundle SoundVolumeView, remove the `--add-binary` option.

## Files

- `main.py` - application entry point
- `ui.py` - CustomTkinter/Tkinter UI and tray menu
- `device_manager.py` - Windows audio device enumeration and switching
- `config.py` - config and preferred-device persistence
- `requirements.txt` - Python dependencies

## Notes

- `config.json` stores local preferred device IDs and names, so it is ignored by git.
- `audio_switcher.log` contains local runtime logs, so it is ignored by git.
- Administrator rights should not be required for normal device switching.

## License

MIT License. See [LICENSE](LICENSE).
