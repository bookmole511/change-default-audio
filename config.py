"""Configuration persistence for Windows Audio Device Switcher."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "audio_switcher.log"


@dataclass(slots=True)
class WindowState:
    width: int = 900
    height: int = 620
    x: int | None = None
    y: int | None = None


@dataclass(slots=True)
class PreferredDevice:
    """A saved endpoint using SoundVolumeView's command-line friendly ID plus display name."""

    id: str
    name: str


@dataclass(slots=True)
class AppConfig:
    window: WindowState = field(default_factory=WindowState)
    auto_refresh: bool = True
    preferred_playback: list[PreferredDevice] = field(default_factory=list)
    preferred_recording: list[PreferredDevice] = field(default_factory=list)
    sound_volume_view_path: str = "SoundVolumeView.exe"


def setup_logging() -> None:
    """Configure file logging once for the whole application."""

    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )
    # pycaw can emit noisy non-fatal property warnings while enumerating devices.
    logging.getLogger("pycaw").setLevel(logging.ERROR)


def _coerce_window(data: dict[str, Any]) -> WindowState:
    return WindowState(
        width=int(data.get("width", 900)),
        height=int(data.get("height", 620)),
        x=data.get("x"),
        y=data.get("y"),
    )


def _coerce_preferred(value: Any) -> PreferredDevice | None:
    if isinstance(value, dict):
        device_id = str(value.get("id", "")).strip()
        name = str(value.get("name", "")).strip() or device_id
        return PreferredDevice(device_id, name) if device_id else None
    if isinstance(value, str) and value.strip():
        return PreferredDevice(value.strip(), value.strip())
    return None


def _coerce_preferred_list(value: Any) -> list[PreferredDevice]:
    if not isinstance(value, list):
        return []

    devices: list[PreferredDevice] = []
    seen: set[str] = set()
    for item in value:
        device = _coerce_preferred(item)
        if device and device.id not in seen:
            devices.append(device)
            seen.add(device.id)
    return devices


def load_config() -> AppConfig:
    """Load config.json, returning defaults if the file is missing or invalid."""

    if not CONFIG_PATH.exists():
        return AppConfig()

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config = AppConfig(
            window=_coerce_window(raw.get("window", {})),
            auto_refresh=bool(raw.get("auto_refresh", True)),
            preferred_playback=_coerce_preferred_list(raw.get("preferred_playback", [])),
            preferred_recording=_coerce_preferred_list(raw.get("preferred_recording", [])),
            sound_volume_view_path=str(raw.get("sound_volume_view_path", "SoundVolumeView.exe")),
        )
        return config
    except Exception:
        logging.exception("Failed to load config; using defaults")
        return AppConfig()


def save_config(config: AppConfig) -> None:
    """Persist application configuration using UTF-8 for Korean device names."""

    payload = asdict(config)
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def preferred_devices(config: AppConfig, kind_value: str) -> list[PreferredDevice]:
    return config.preferred_playback if kind_value == "playback" else config.preferred_recording


def upsert_preferred_device(config: AppConfig, kind_value: str, device_id: str, name: str) -> PreferredDevice:
    """Insert or update a preferred device while preserving user-friendly names."""

    target = preferred_devices(config, kind_value)
    for index, existing in enumerate(target):
        if existing.id == device_id:
            target[index] = PreferredDevice(device_id, name)
            return target[index]

    preferred = PreferredDevice(device_id, name)
    target.append(preferred)
    return preferred

