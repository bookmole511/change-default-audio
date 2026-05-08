"""CustomTkinter UI for Windows Audio Device Switcher."""

from __future__ import annotations

import logging
import socket
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable

try:
    import customtkinter as ctk
except Exception:  # pragma: no cover - fallback keeps the app runnable.
    ctk = None  # type: ignore[assignment]

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - tray is optional.
    pystray = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

from config import (
    AppConfig,
    current_startup_command,
    has_preferred_devices,
    save_config,
    set_startup_enabled,
    startup_command,
    upsert_preferred_device,
)
from device_manager import AudioDevice, AudioDeviceManager, DeviceKind


LOGGER = logging.getLogger(__name__)
MISSING_WARNING = "저장된 장치 '{device_name}'가 현재 연결되어 있지 않습니다."

STATUS_TEXT: dict[str, str] = {
    "Both": "\u25cf Both",
    "Default": "\u25b6 Default",
    "Communications": "\u260e Communications",
    "Not found": "Not found",
}
STATUS_COLORS: dict[str, str] = {
    "Both": "#00CC00",
    "Default": "#3399FF",
    "Communications": "#FFAA00",
    "Not found": "#8A8F98",
    "Normal": "#E8EAED",
}

SAVED_TEXT = "✓ Saved"
NOT_SAVED_TEXT = "Off"
STARTUP_LABEL = "Windows 시작 시 자동 실행"
REGISTER_MODE_LABEL = "장비 등록"
SWITCH_MODE_LABEL = "장비 스위칭"
REGISTER_MODE_DESCRIPTION = "더블클릭하여 사용할 장치로 등록할 수 있습니다. 등록된 장치는 스위칭 모드에서 설정해주세요."
SWITCH_MODE_DESCRIPTION = "더블클릭하여 Both로 설정할 수 있습니다."
FIRST_TIME_HELP = "등록하고 싶은 장치를 더블클릭하여 장비로 등록하세요."


class AudioSwitcherApp:
    """Main application window."""

    def __init__(
        self,
        manager: AudioDeviceManager,
        config: AppConfig,
        start_minimized: bool = False,
        ipc_socket: socket.socket | None = None,
    ) -> None:
        self.manager = manager
        self.config = config
        self.start_minimized = start_minimized
        self.ipc_socket = ipc_socket
        self.ipc_running = False
        self.devices: dict[DeviceKind, list[AudioDevice]] = {
            DeviceKind.PLAYBACK: [],
            DeviceKind.RECORDING: [],
        }
        self.selected: dict[DeviceKind, AudioDevice | None] = {
            DeviceKind.PLAYBACK: None,
            DeviceKind.RECORDING: None,
        }
        self.view_toggle_buttons: dict[DeviceKind, object] = {}
        self.mode_description_vars: dict[DeviceKind, tk.StringVar] = {}
        self.treeviews: dict[DeviceKind, ttk.Treeview] = {}
        self.missing_warned: set[str] = set()
        self.tray_icon: pystray.Icon | None = None if pystray else None

        if ctk:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()

        self.root.title("Windows Audio Device Switcher")
        self._restore_geometry()
        self.root.minsize(860, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_controller)
        if self.start_minimized:
            self.root.withdraw()

        self.normal_font = tkfont.Font(family="Segoe UI", size=11)
        self.bold_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self._configure_tree_style()

        self.status_var = tk.StringVar(value="Ready")
        self.startup_var = tk.BooleanVar(value=self.config.start_with_windows)
        self.show_all_vars: dict[DeviceKind, tk.BooleanVar] = {
            DeviceKind.PLAYBACK: tk.BooleanVar(value=not has_preferred_devices(self.config, DeviceKind.PLAYBACK.value)),
            DeviceKind.RECORDING: tk.BooleanVar(value=not has_preferred_devices(self.config, DeviceKind.RECORDING.value)),
        }

        self._build_ui()
        self._apply_initial_startup_preference()
        self.refresh_all()
        self._start_tray_icon()
        self._start_ipc_server()

    def run(self) -> None:
        self.root.mainloop()

    def _configure_tree_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Device.Treeview",
            background="#171A21",
            foreground=STATUS_COLORS["Normal"],
            fieldbackground="#171A21",
            rowheight=32,
            borderwidth=0,
            font=self.normal_font,
        )
        style.configure(
            "Device.Treeview.Heading",
            background="#242832",
            foreground="#F5F7FA",
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Device.Treeview",
            background=[("selected", "#2D5F8B")],
            foreground=[("selected", "#FFFFFF")],
        )

    def _build_ui(self) -> None:
        if ctk:
            self._build_ctk_ui()
        else:
            self._build_tk_ui()

    def _build_ctk_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        tabview = ctk.CTkTabview(self.root)
        tabview.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="nsew")
        self._build_device_tab(tabview.add("Playback"), DeviceKind.PLAYBACK)
        self._build_device_tab(tabview.add("Recording"), DeviceKind.RECORDING)

        footer = ctk.CTkFrame(self.root, corner_radius=0)
        footer.grid(row=1, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, textvariable=self.status_var, anchor="w").grid(row=0, column=0, padx=12, pady=8, sticky="ew")
        ctk.CTkCheckBox(
            footer,
            text=STARTUP_LABEL,
            variable=self.startup_var,
            command=self._on_startup_changed,
        ).grid(row=0, column=1, padx=12, pady=8)

    def _build_tk_ui(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        for title, kind in (("Playback", DeviceKind.PLAYBACK), ("Recording", DeviceKind.RECORDING)):
            frame = tk.Frame(notebook)
            notebook.add(frame, text=title)
            self._build_device_tab(frame, kind)
        footer = tk.Frame(self.root)
        footer.grid(row=1, column=0, sticky="ew")
        tk.Label(footer, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Checkbutton(footer, text=STARTUP_LABEL, variable=self.startup_var, command=self._on_startup_changed).pack(
            side="right", padx=8
        )

    def _build_device_tab(self, parent: object, kind: DeviceKind) -> None:
        parent.grid_columnconfigure(0, weight=1)  # type: ignore[attr-defined]
        parent.grid_rowconfigure(1, weight=1)  # type: ignore[attr-defined]

        toolbar = self._frame(parent)
        toolbar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        toolbar.grid_columnconfigure(5, weight=1)

        toggle = self._button(toolbar, self._mode_button_text(kind), lambda k=kind: self._toggle_device_view(k))
        toggle.grid(row=0, column=0, padx=(0, 8), pady=6)
        self.view_toggle_buttons[kind] = toggle

        description_var = tk.StringVar(value=self._mode_description(kind))
        self.mode_description_vars[kind] = description_var
        self._label(toolbar, description_var).grid(row=1, column=0, columnspan=5, padx=(0, 8), pady=(0, 6), sticky="w")

        self._button(toolbar, "새로고침", lambda k=kind: self.refresh(k)).grid(row=0, column=1, padx=4, pady=6)
        self._button(toolbar, "Default", lambda k=kind: self._act(k, self.manager.set_default)).grid(row=0, column=2, padx=4, pady=6)
        self._button(toolbar, "Communication", lambda k=kind: self._act(k, self.manager.set_communications)).grid(
            row=0, column=3, padx=4, pady=6
        )
        self._button(toolbar, "Both", lambda k=kind: self._act(k, self.manager.set_both)).grid(row=0, column=4, padx=4, pady=6)

        tree = ttk.Treeview(parent, columns=("device", "role"), show="headings", selectmode="browse", style="Device.Treeview")
        tree.heading("device", text="Device")
        tree.heading("role", text="Current Role")
        tree.column("device", width=620, minwidth=280, stretch=True, anchor="w")
        tree.column("role", width=180, minwidth=150, stretch=False, anchor="center")
        tree.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        tree.bind("<<TreeviewSelect>>", lambda _event, device_kind=kind: self._on_select(device_kind))
        tree.bind("<Double-1>", lambda _event, device_kind=kind: self._on_double_click(device_kind))
        self._configure_tree_tags(tree)
        self.treeviews[kind] = tree

    def _configure_tree_tags(self, tree: ttk.Treeview) -> None:
        tree.tag_configure("normal", foreground=STATUS_COLORS["Normal"], font=self.normal_font)
        tree.tag_configure("both", foreground=STATUS_COLORS["Both"], font=self.bold_font)
        tree.tag_configure("default", foreground=STATUS_COLORS["Default"], font=self.bold_font)
        tree.tag_configure("communications", foreground=STATUS_COLORS["Communications"], font=self.bold_font)
        tree.tag_configure("missing", foreground=STATUS_COLORS["Not found"], font=self.normal_font)
        tree.tag_configure("saved", foreground=STATUS_COLORS["Both"], font=self.bold_font)

    def _frame(self, parent: object) -> object:
        return ctk.CTkFrame(parent) if ctk else tk.Frame(parent)

    def _button(self, parent: object, text: str, command: Callable[[], None]) -> object:
        return ctk.CTkButton(parent, text=text, command=command) if ctk else tk.Button(parent, text=text, command=command)

    def _label(self, parent: object, textvariable: tk.StringVar) -> object:
        if ctk:
            return ctk.CTkLabel(parent, textvariable=textvariable, anchor="w", text_color="#AAB2BD")
        return tk.Label(parent, textvariable=textvariable, anchor="w", fg="#666666")

    def refresh_all(self) -> None:
        self.refresh(DeviceKind.PLAYBACK)
        self.refresh(DeviceKind.RECORDING)

    def refresh(self, kind: DeviceKind) -> None:
        try:
            self.devices[kind] = self.manager.get_all_devices(kind) if self.show_all_vars[kind].get() else self.manager.get_preferred_devices(kind)
            self._update_tree_heading(kind)
            self._update_toggle_label(kind)
            self._render_devices(kind)
            if self.show_all_vars[kind].get() and not has_preferred_devices(self.config, kind.value):
                self._set_status(FIRST_TIME_HELP)
            else:
                mode = REGISTER_MODE_LABEL if self.show_all_vars[kind].get() else SWITCH_MODE_LABEL
                self._set_status(f"{mode}: {len(self.devices[kind])}개 장치를 불러왔습니다.")
            self._warn_missing_devices(kind)
        except Exception as exc:
            LOGGER.exception("Failed to refresh %s devices", kind.value)
            self._set_status(f"오류: {exc}")

    def _render_devices(self, kind: DeviceKind) -> None:
        tree = self.treeviews[kind]
        tree.delete(*tree.get_children())
        self.selected[kind] = None

        for index, device in enumerate(self.devices[kind]):
            if self.show_all_vars[kind].get():
                saved = self.manager.is_saved_device(device)
                role_text = SAVED_TEXT if saved else NOT_SAVED_TEXT
                device_text = f"{device.name} ({role_text})"
                tag = "saved" if saved else "normal"
            else:
                role_text = self._status_text(device)
                device_text = f"{device.name} ({role_text})" if role_text else device.name
                tag = self._status_tag(device)
            tree.insert("", "end", iid=str(index), values=(device_text, role_text or "-"), tags=(tag,))

        if not self.devices[kind] and not self.show_all_vars[kind].get():
            tree.insert("", "end", iid="empty", values=("저장된 장치가 없습니다. 장비 등록 모드에서 더블클릭해 등록하세요.", "-"), tags=("missing",))

    def _status_text(self, device: AudioDevice) -> str:
        return STATUS_TEXT.get(device.status, "")

    @staticmethod
    def _status_tag(device: AudioDevice) -> str:
        if device.is_missing:
            return "missing"
        if device.status == "Both":
            return "both"
        if device.status == "Default":
            return "default"
        if device.status == "Communications":
            return "communications"
        return "normal"

    def _toggle_device_view(self, kind: DeviceKind) -> None:
        self.show_all_vars[kind].set(not self.show_all_vars[kind].get())
        self._update_toggle_label(kind)
        self._update_tree_heading(kind)
        self.refresh(kind)

    def _update_toggle_label(self, kind: DeviceKind) -> None:
        button = self.view_toggle_buttons.get(kind)
        if button:
            text = self._mode_button_text(kind)
            if ctk and hasattr(button, "configure"):
                button.configure(text=text)
            elif hasattr(button, "config"):
                button.config(text=text)
        description = self.mode_description_vars.get(kind)
        if description:
            description.set(self._mode_description(kind))

    def _update_tree_heading(self, kind: DeviceKind) -> None:
        tree = self.treeviews.get(kind)
        if tree:
            tree.heading("role", text="Saved" if self.show_all_vars[kind].get() else "Current Role")

    def _mode_button_text(self, kind: DeviceKind) -> str:
        return SWITCH_MODE_LABEL if self.show_all_vars[kind].get() else REGISTER_MODE_LABEL

    def _mode_description(self, kind: DeviceKind) -> str:
        if self.show_all_vars[kind].get():
            return FIRST_TIME_HELP if not has_preferred_devices(self.config, kind.value) else REGISTER_MODE_DESCRIPTION
        return SWITCH_MODE_DESCRIPTION

    def _warn_missing_devices(self, kind: DeviceKind) -> None:
        for device in self.devices[kind]:
            if not device.is_missing or device.id in self.missing_warned:
                continue
            self.missing_warned.add(device.id)
            message = MISSING_WARNING.format(device_name=device.name)
            self._set_status(message)
            messagebox.showwarning("저장된 장치 없음", message)

    def _on_select(self, kind: DeviceKind) -> None:
        device = self._selected_tree_device(kind)
        self.selected[kind] = device
        if device and device.is_missing:
            self._set_status(MISSING_WARNING.format(device_name=device.name))

    def _on_double_click(self, kind: DeviceKind) -> None:
        device = self._selected_tree_device(kind)
        if not device:
            return
        self.selected[kind] = device
        if self.show_all_vars[kind].get():
            self._toggle_saved_device(kind, device)
            return
        self._set_both_device(kind, device, success_prefix="Set Both")

    def _selected_tree_device(self, kind: DeviceKind) -> AudioDevice | None:
        selection = self.treeviews[kind].selection()
        if not selection:
            return None
        item_id = selection[0]
        if not item_id.isdigit():
            return None
        index = int(item_id)
        return self.devices[kind][index] if index < len(self.devices[kind]) else None

    def _act(self, kind: DeviceKind, action: Callable[[AudioDevice], None]) -> None:
        device = self.selected.get(kind)
        if not device:
            self._set_status("장치를 먼저 선택하세요.")
            return
        if getattr(action, "__name__", "") == "set_both":
            self._set_both_device(kind, device, success_prefix="Set Both")
            return
        self._apply_device_action(kind, device, action, success_message=f"설정 완료: {device.name}")

    def _set_both_device(self, kind: DeviceKind, device: AudioDevice, success_prefix: str) -> None:
        self._apply_device_action(kind, device, self.manager.set_both, success_message=f"{success_prefix}: {device.name}")

    def _apply_device_action(
        self,
        kind: DeviceKind,
        device: AudioDevice,
        action: Callable[[AudioDevice], None],
        success_message: str,
    ) -> None:
        if device.is_missing:
            message = MISSING_WARNING.format(device_name=device.name)
            self._set_status(message)
            messagebox.showwarning("저장된 장치 없음", message)
            return
        try:
            action(device)
            self._remember(kind, device)
            self.refresh(kind)
            self._set_status(success_message)
        except Exception as exc:
            LOGGER.exception("Failed to apply setting to %s", device.name)
            self._set_status(f"오류: {exc}")

    def _remember(self, kind: DeviceKind, device: AudioDevice) -> None:
        upsert_preferred_device(self.config, kind.value, device.id, device.name)
        save_config(self.config)
        self._refresh_tray_menu()

    def _toggle_saved_device(self, kind: DeviceKind, device: AudioDevice) -> None:
        if self.manager.is_saved_device(device):
            removed = self.manager.remove_saved_device(device)
            if removed:
                save_config(self.config)
                self.refresh(kind)
                self._refresh_tray_menu()
                self._set_status(f"저장된 장치에서 제거되었습니다: {device.name}")
            return

        self.manager.save_device(device)
        save_config(self.config)
        self.refresh(kind)
        self._refresh_tray_menu()
        self._set_status(f"장치가 저장되었습니다: {device.name}")

    def _apply_initial_startup_preference(self) -> None:
        try:
            if self.config.start_with_windows and current_startup_command() != startup_command():
                set_startup_enabled(True)
        except Exception:
            LOGGER.exception("Failed to apply startup preference")
            self._set_status("시작 프로그램 등록에 실패했습니다.")

    def _on_startup_changed(self) -> None:
        enabled = bool(self.startup_var.get())
        try:
            set_startup_enabled(enabled)
            self.config.start_with_windows = enabled
            save_config(self.config)
            self._set_status("시작 프로그램 자동 실행이 켜졌습니다." if enabled else "시작 프로그램 자동 실행이 꺼졌습니다.")
        except Exception as exc:
            LOGGER.exception("Failed to update startup registration")
            self.startup_var.set(not enabled)
            self._set_status(f"시작 프로그램 설정 오류: {exc}")

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _restore_geometry(self) -> None:
        window = self.config.window
        geometry = f"{window.width}x{window.height}"
        if window.x is not None and window.y is not None:
            geometry += f"+{window.x}+{window.y}"
        self.root.geometry(geometry)

    def _save_geometry(self) -> None:
        self.root.update_idletasks()
        self.config.window.width = self.root.winfo_width()
        self.config.window.height = self.root.winfo_height()
        self.config.window.x = self.root.winfo_x()
        self.config.window.y = self.root.winfo_y()
        save_config(self.config)

    def _start_tray_icon(self) -> None:
        if not pystray or Image is None or ImageDraw is None:
            LOGGER.info("pystray/Pillow not available; tray icon disabled")
            return
        if self.tray_icon is not None:
            return

        image = Image.new("RGBA", (64, 64), (20, 24, 32, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((14, 18, 30, 46), fill=(77, 171, 247, 255))
        draw.polygon([(31, 22), (48, 14), (48, 50), (31, 42)], fill=(77, 171, 247, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Open Controller", lambda icon, item: self.root.after(0, self._show_window)),
            pystray.Menu.SEPARATOR,
            self._tray_role_submenu_item("Both", "both"),
            self._tray_role_submenu_item("Communication", "communication"),
            self._tray_role_submenu_item("Default", "default"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, item: self.root.after(0, self.exit_application)),
        )
        self.tray_icon = pystray.Icon("audio_switcher", image, "Windows Audio Device Switcher", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _refresh_tray_menu(self) -> None:
        """Force pystray to rebuild cached Windows menu items after saved devices change."""

        if not self.tray_icon:
            return
        try:
            self.tray_icon.update_menu()
        except Exception:
            LOGGER.exception("Failed to refresh tray menu")

    def _tray_role_submenu_item(self, label: str, role: str) -> pystray.MenuItem:
        submenu = pystray.Menu(lambda r=role: self._create_role_submenu(r))
        try:
            return pystray.MenuItem(label, submenu, submenu=True)
        except TypeError:
            return pystray.MenuItem(label, submenu)

    def _create_role_submenu(self, role: str) -> list[pystray.MenuItem]:
        if not pystray:
            return []

        try:
            devices = [
                *self.manager.get_saved_devices(DeviceKind.PLAYBACK),
                *self.manager.get_saved_devices(DeviceKind.RECORDING),
            ]
        except Exception:
            LOGGER.exception("Failed to build %s tray submenu", role)
            return [pystray.MenuItem("Unable to load devices", lambda icon, item: None, enabled=False)]

        if not devices:
            return [pystray.MenuItem("No saved devices", lambda icon, item: None, enabled=False)]

        return [
            pystray.MenuItem(
                self._tray_device_label(dev),
                lambda icon, item, *, d=dev, r=role: self.root.after(0, lambda: self._tray_apply_role(d, r)),
            )
            for dev in devices[:20]
        ]

    def _tray_device_label(self, device: AudioDevice) -> str:
        status = self._status_text(device)
        return f"{device.name} ({status})" if status else device.name

    def _tray_apply_role(self, device: AudioDevice, role: str) -> None:
        if not self.manager.is_device_available(device):
            message = "선택한 오디오 장치가 현재 연결되어 있지 않습니다."
            self._set_status(message)
            messagebox.showwarning("Audio Device Not Available", message)
            return

        actions: dict[str, tuple[Callable[[AudioDevice], None], str]] = {
            "both": (self.manager.set_both, "Set Both"),
            "communication": (self.manager.set_communications, "Set Communications"),
            "default": (self.manager.set_default, "Set Default"),
        }
        action, label = actions[role]
        self._apply_device_action(device.kind, device, action, success_message=f"{label}: {device.name}")

    def _start_ipc_server(self) -> None:
        if self.ipc_socket is None:
            return
        self.ipc_running = True
        threading.Thread(target=self._ipc_loop, daemon=True).start()

    def _ipc_loop(self) -> None:
        while self.ipc_running and self.ipc_socket is not None:
            try:
                conn, _addr = self.ipc_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    command = conn.recv(64).strip().upper()
                except OSError:
                    continue
            if command == b"SHOW":
                self.root.after(0, self._show_window)

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide_controller(self) -> None:
        self._save_geometry()
        self.root.withdraw()
        self._set_status("컨트롤러 창이 숨겨졌습니다. 트레이에서 다시 열 수 있습니다.")

    def exit_application(self) -> None:
        self.ipc_running = False
        if self.ipc_socket is not None:
            try:
                self.ipc_socket.close()
            except OSError:
                pass
            self.ipc_socket = None
        self._save_geometry()
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()
