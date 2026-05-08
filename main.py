r"""Entry point for Windows Audio Device Switcher."""

from __future__ import annotations

import ctypes
import logging
import socket
import sys
from tkinter import messagebox

from config import load_config, setup_logging
from device_manager import AudioDeviceManager
from ui import AudioSwitcherApp


LOGGER = logging.getLogger(__name__)
IPC_HOST = "127.0.0.1"
IPC_PORT = 48731
IPC_COMMAND_SHOW = b"SHOW\n"


def notify_existing_instance() -> bool:
    """Ask an already-running instance to show its controller window.

    Returns True when another instance answered the IPC port. The current
    process should then exit without creating a second tray icon.
    """

    try:
        with socket.create_connection((IPC_HOST, IPC_PORT), timeout=0.35) as client:
            client.sendall(IPC_COMMAND_SHOW)
        return True
    except OSError:
        return False


def create_ipc_socket() -> socket.socket | None:
    """Reserve the local IPC port used for single-instance control."""

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((IPC_HOST, IPC_PORT))
        server.listen(4)
        server.settimeout(0.5)
        return server
    except OSError:
        server.close()
        if notify_existing_instance():
            return None
        raise


def main() -> int:
    setup_logging()
    if sys.platform != "win32":
        messagebox.showerror("Unsupported OS", "Windows Audio Device Switcher supports Windows 10/11 only.")
        return 1

    if notify_existing_instance():
        return 0

    try:
        ipc_socket = create_ipc_socket()
    except OSError:
        LOGGER.exception("Failed to create single-instance IPC socket")
        messagebox.showerror("실행 오류", "프로그램 단일 실행 잠금을 만들 수 없습니다.")
        return 1

    if ipc_socket is None:
        return 0

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        LOGGER.debug("DPI awareness setup skipped", exc_info=True)

    config = load_config()
    manager = AudioDeviceManager(config.sound_volume_view_path, config)
    start_minimized = "--minimized" in sys.argv or "--tray" in sys.argv
    app = AudioSwitcherApp(manager, config, start_minimized=start_minimized, ipc_socket=ipc_socket)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
