r"""Entry point for Windows Audio Device Switcher.

Setup:
  1. Create a virtual environment:
       py -m venv .venv
       .venv\Scripts\activate
  2. Install dependencies:
       pip install -r requirements.txt
  3. Run:
       py main.py

SoundVolumeView fallback:
  Download SoundVolumeView from NirSoft and place SoundVolumeView.exe next to
  main.py, or set "sound_volume_view_path" in config.json. Its documented
  command format is:
       SoundVolumeView.exe /SetDefault "<Device Name or Command-Line Friendly ID>" all

PyInstaller:
  pyinstaller --noconfirm --windowed --name "WindowsAudioDeviceSwitcher" ^
    --add-binary "SoundVolumeView.exe;." main.py

Equivalent .spec essentials:
  datas=[],
  binaries=[("SoundVolumeView.exe", ".")],
  hiddenimports=["comtypes.stream", "pycaw.pycaw", "pystray", "PIL.Image", "PIL.ImageDraw"]
"""

from __future__ import annotations

import ctypes
import logging
import sys
from tkinter import messagebox

from config import load_config, setup_logging
from device_manager import AudioDeviceManager
from ui import AudioSwitcherApp


LOGGER = logging.getLogger(__name__)


def main() -> int:
    setup_logging()
    if sys.platform != "win32":
        messagebox.showerror("Unsupported OS", "Windows Audio Device Switcher supports Windows 10/11 only.")
        return 1

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        LOGGER.debug("DPI awareness setup skipped", exc_info=True)

    config = load_config()
    manager = AudioDeviceManager(config.sound_volume_view_path, config)
    app = AudioSwitcherApp(manager, config)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
