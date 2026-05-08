"""Audio device enumeration and default-device switching.

The preferred path uses pycaw/comtypes to call Windows Core Audio APIs. Setting
default devices uses the undocumented but widely used PolicyConfig COM object.
If that fails, the manager falls back to NirSoft SoundVolumeView.exe.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import warnings
from ctypes import c_int, c_wchar_p
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.client import CreateObject

from config import AppConfig, preferred_devices

try:
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import AudioUtilities, IMMDeviceEnumerator
except Exception:  # pragma: no cover - import failure is handled at runtime.
    AudioUtilities = None  # type: ignore[assignment]
    IMMDeviceEnumerator = None  # type: ignore[assignment]
    CLSID_MMDeviceEnumerator = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)
logging.getLogger("pycaw").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module=r"pycaw(\.|$)")


class DeviceKind(str, Enum):
    PLAYBACK = "playback"
    RECORDING = "recording"


class AudioRole(int, Enum):
    CONSOLE = 0
    MULTIMEDIA = 1
    COMMUNICATIONS = 2


@dataclass(frozen=True, slots=True)
class AudioDevice:
    id: str
    name: str
    kind: DeviceKind
    is_default: bool = False
    is_communications: bool = False
    is_missing: bool = False

    @property
    def status(self) -> str:
        if self.is_missing:
            return "Not found"
        if self.is_default and self.is_communications:
            return "Both"
        if self.is_default:
            return "Default"
        if self.is_communications:
            return "Communications"
        return ""


class IPolicyConfig(IUnknown):
    """Minimal PolicyConfig interface needed for SetDefaultEndpoint."""

    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetMixFormat"),
        COMMETHOD([], HRESULT, "GetDeviceFormat"),
        COMMETHOD([], HRESULT, "ResetDeviceFormat"),
        COMMETHOD([], HRESULT, "SetDeviceFormat"),
        COMMETHOD([], HRESULT, "GetProcessingPeriod"),
        COMMETHOD([], HRESULT, "SetProcessingPeriod"),
        COMMETHOD([], HRESULT, "GetShareMode"),
        COMMETHOD([], HRESULT, "SetShareMode"),
        COMMETHOD([], HRESULT, "GetPropertyValue"),
        COMMETHOD([], HRESULT, "SetPropertyValue"),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], c_wchar_p, "wszDeviceId"), (["in"], c_int, "role")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility"),
    ]


CLSID_POLICY_CONFIG = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


class AudioDeviceManager:
    """Enumerates and switches Windows audio endpoints."""

    def __init__(self, sound_volume_view_path: str = "SoundVolumeView.exe", config: AppConfig | None = None) -> None:
        self.sound_volume_view_path = sound_volume_view_path
        self.config = config

    def list_devices(self, kind: DeviceKind) -> list[AudioDevice]:
        """Backward-compatible alias for all active endpoints."""

        return self.get_all_devices(kind)

    def get_all_devices(self, kind: DeviceKind) -> list[AudioDevice]:
        """Return active playback or recording endpoints with safe per-device error handling."""

        self._ensure_windows()
        flow = self._flow_value(kind)
        default_ids = self._get_default_ids(kind)

        if AudioUtilities is None:
            raise RuntimeError("pycaw is not installed. Install requirements.txt dependencies.")

        devices: list[AudioDevice] = []
        try:
            raw_devices = AudioUtilities.GetAllDevices()
        except Exception as exc:
            LOGGER.exception("Failed to enumerate audio devices")
            raise RuntimeError(f"Could not enumerate audio devices: {exc}") from exc

        for raw_device in raw_devices:
            try:
                if not self._is_active_endpoint(raw_device, flow):
                    continue
                device_id = self._read_text_property(raw_device, ("id", "Id"))
                name = self._read_text_property(raw_device, ("FriendlyName", "name")) or device_id
                if not device_id:
                    LOGGER.warning("Skipped %s device without an endpoint ID: %r", kind.value, raw_device)
                    continue

                devices.append(
                    AudioDevice(
                        id=device_id,
                        name=name,
                        kind=kind,
                        is_default=device_id in {
                            default_ids.get(AudioRole.CONSOLE),
                            default_ids.get(AudioRole.MULTIMEDIA),
                        },
                        is_communications=device_id == default_ids.get(AudioRole.COMMUNICATIONS),
                    )
                )
            except Exception as exc:
                LOGGER.warning("Skipped one %s audio device during enumeration: %r", kind.value, raw_device, exc_info=exc)
                continue

        devices.sort(key=lambda item: (item.status == "", item.name.casefold()))
        return devices

    def get_preferred_devices(self, kind: DeviceKind) -> list[AudioDevice]:
        """Return saved devices first; missing saved devices are included as disabled placeholders."""

        if self.config is None:
            return self.get_all_devices(kind)

        all_devices = {device.id: device for device in self.get_all_devices(kind)}
        result: list[AudioDevice] = []
        seen: set[str] = set()
        for preferred in preferred_devices(self.config, kind.value):
            current = all_devices.get(preferred.id)
            if current:
                result.append(current)
            else:
                result.append(AudioDevice(preferred.id, preferred.name, kind, is_missing=True))
            seen.add(preferred.id)
        return result

    def set_default(self, device: AudioDevice) -> None:
        """Set the normal default device. Console and multimedia are updated."""

        self._reject_missing(device)
        self._set_roles(device, [AudioRole.CONSOLE, AudioRole.MULTIMEDIA])

    def set_communications(self, device: AudioDevice) -> None:
        """Set the communications role used by voice-chat applications."""

        self._reject_missing(device)
        self._set_roles(device, [AudioRole.COMMUNICATIONS])

    def set_both(self, device: AudioDevice) -> None:
        """Set console, multimedia, and communications roles to the same device."""

        self._reject_missing(device)
        self._set_roles(device, [AudioRole.CONSOLE, AudioRole.MULTIMEDIA, AudioRole.COMMUNICATIONS])

    def _set_roles(self, device: AudioDevice, roles: Iterable[AudioRole]) -> None:
        role_list = list(roles)
        try:
            self._set_roles_with_policy_config(device.id, role_list)
            LOGGER.info("Set %s roles %s through PolicyConfig", device.name, role_list)
        except Exception as exc:
            LOGGER.exception("PolicyConfig failed for %s; trying SoundVolumeView", device.name)
            self._set_roles_with_sound_volume_view(device, role_list, exc)

    def _set_roles_with_policy_config(self, device_id: str, roles: Iterable[AudioRole]) -> None:
        policy = CreateObject(CLSID_POLICY_CONFIG, interface=IPolicyConfig)
        for role in roles:
            policy.SetDefaultEndpoint(device_id, int(role))

    def _set_roles_with_sound_volume_view(
        self,
        device: AudioDevice,
        roles: list[AudioRole],
        original_error: Exception,
    ) -> None:
        executable = self._resolve_sound_volume_view_path()
        if not executable:
            raise RuntimeError(
                "Direct COM switching failed and SoundVolumeView.exe was not found. "
                "Place SoundVolumeView.exe next to main.py or set sound_volume_view_path in config.json."
            ) from original_error

        role_arg = "all" if set(roles) == {AudioRole.CONSOLE, AudioRole.MULTIMEDIA, AudioRole.COMMUNICATIONS} else None
        if role_arg:
            self._run_sound_volume_view(executable, device, role_arg)
            return

        for role in roles:
            self._run_sound_volume_view(executable, device, str(int(role)))

    def _run_sound_volume_view(self, executable: Path, device: AudioDevice, role: str) -> None:
        command = [str(executable), "/SetDefault", device.id or device.name, role]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        if completed.returncode != 0:
            LOGGER.error("SoundVolumeView failed: %s stderr=%s", command, completed.stderr)
            raise RuntimeError(f"SoundVolumeView failed with exit code {completed.returncode}: {completed.stderr}")
        LOGGER.info("SoundVolumeView set %s role %s", device.name, role)

    def _resolve_sound_volume_view_path(self) -> Path | None:
        candidates = [
            Path(self.sound_volume_view_path),
            Path(__file__).resolve().parent / "SoundVolumeView.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _get_default_ids(self, kind: DeviceKind) -> dict[AudioRole, str | None]:
        if IMMDeviceEnumerator is None or CLSID_MMDeviceEnumerator is None:
            return {role: None for role in AudioRole}

        flow = self._flow_value(kind)
        result: dict[AudioRole, str | None] = {}
        try:
            enumerator = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL)
        except Exception:
            LOGGER.exception("Failed to create MMDeviceEnumerator")
            return {role: None for role in AudioRole}

        for role in AudioRole:
            try:
                endpoint = enumerator.GetDefaultAudioEndpoint(flow, int(role))
                result[role] = str(endpoint.GetId())
            except Exception:
                result[role] = None
        return result

    @staticmethod
    def _read_text_property(raw_device: object, names: tuple[str, ...]) -> str:
        for name in names:
            try:
                value = getattr(raw_device, name, "")
                if value:
                    return str(value)
            except Exception:
                LOGGER.debug("Failed reading pycaw property %s from %r", name, raw_device, exc_info=True)
        return ""

    @staticmethod
    def _flow_value(kind: DeviceKind) -> int:
        return 0 if kind is DeviceKind.PLAYBACK else 1

    @staticmethod
    def _is_active_endpoint(raw_device: object, flow: int) -> bool:
        try:
            device_flow = getattr(raw_device, "data_flow", getattr(raw_device, "DataFlow", None))
            if hasattr(device_flow, "value"):
                device_flow = device_flow.value
            if device_flow is not None and int(device_flow) != flow:
                return False

            state = getattr(raw_device, "state", getattr(raw_device, "State", 1))
            if hasattr(state, "value"):
                state = state.value
            return int(state) == 1
        except Exception:
            LOGGER.debug("Failed checking endpoint state/flow for %r", raw_device, exc_info=True)
            return False

    @staticmethod
    def _reject_missing(device: AudioDevice) -> None:
        if device.is_missing:
            raise RuntimeError(f"Saved device '{device.name}' is not currently connected or available.")

    @staticmethod
    def _ensure_windows() -> None:
        if sys.platform != "win32":
            raise RuntimeError("This application supports Windows 10/11 only.")
