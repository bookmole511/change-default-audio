"""CustomTkinter UI for Windows Audio Device Switcher."""

from __future__ import annotations

import logging
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
    has_preferred_devices,
    is_startup_enabled,
    save_config,
    set_startup_enabled,
    upsert_preferred_device,
)
from device_manager import AudioDevice, AudioDeviceManager, DeviceKind


LOGGER = logging.getLogger(__name__)
AUTO_REFRESH_MS = 5000
MISSING_WARNING = "Warning: Saved device '{device_name}' is not currently connected or available."

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


class AudioSwitcherApp:
    """Main application window."""

    def __init__(self, manager: AudioDeviceManager, config: AppConfig, start_minimized: bool = False) -> None:
        self.manager = manager
        self.config = config
        self.start_minimized = start_minimized
        self.devices: dict[DeviceKind, list[AudioDevice]] = {DeviceKind.PLAYBACK: [], DeviceKind.RECORDING: []}
        self.selected: dict[DeviceKind, AudioDevice | None] = {DeviceKind.PLAYBACK: None, DeviceKind.RECORDING: None}
        self.view_toggle_buttons: dict[DeviceKind, object] = {}
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

        self.normal_font = tkfont.Font(family="Segoe UI", size=11)
        self.bold_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self._configure_tree_style()

        self.status_var = tk.StringVar(value="Ready")
        self.auto_refresh_var = tk.BooleanVar(value=self.config.auto_refresh)
        self.startup_var = tk.BooleanVar(value=self.config.start_with_windows)
        self.show_all_vars: dict[DeviceKind, tk.BooleanVar] = {
            DeviceKind.PLAYBACK: tk.BooleanVar(value=not has_preferred_devices(self.config, DeviceKind.PLAYBACK.value)),
            DeviceKind.RECORDING: tk.BooleanVar(value=not has_preferred_devices(self.config, DeviceKind.RECORDING.value)),
        }

        self.treeviews: dict[DeviceKind, ttk.Treeview] = {}
        self._build_ui()
        self._apply_initial_startup_preference()
        self.refresh_all()
        self._start_tray_icon()
        if self.start_minimized:
            self.root.after(0, self.hide_controller)
        self._schedule_auto_refresh()

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
        ctk.CTkSwitch(
            footer,
            text="Auto-refresh",
            variable=self.auto_refresh_var,
            command=self._on_auto_refresh_changed,
        ).grid(row=0, column=1, padx=12, pady=8)
        ctk.CTkCheckBox(
            footer,
            text=STARTUP_LABEL,
            variable=self.startup_var,
            command=self._on_startup_changed,
        ).grid(row=0, column=2, padx=12, pady=8)

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
        tk.Checkbutton(footer, text="Auto-refresh", variable=self.auto_refresh_var, command=self._on_auto_refresh_changed).pack(
            side="right", padx=8
        )
        tk.Checkbutton(footer, text=STARTUP_LABEL, variable=self.startup_var, command=self._on_startup_changed).pack(
            side="right", padx=8
        )

    def _build_device_tab(self, parent: object, kind: DeviceKind) -> None:
        parent.grid_columnconfigure(0, weight=1)  # type: ignore[attr-defined]
        parent.grid_rowconfigure(1, weight=1)  # type: ignore[attr-defined]

        toolbar = self._frame(parent)
        toolbar.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        toolbar.grid_columnconfigure(5, weight=1)

        toggle = self._button(toolbar, "Show All Devices", lambda k=kind: self._toggle_device_view(k))
        toggle.grid(row=0, column=0, padx=(0, 8), pady=6)
        self.view_toggle_buttons[kind] = toggle
        self._button(toolbar, "Refresh", lambda k=kind: self.refresh(k)).grid(row=0, column=1, padx=4, pady=6)
        self._button(toolbar, "Set as Default", lambda k=kind: self._act(k, self.manager.set_default)).grid(
            row=0, column=2, padx=4, pady=6
        )
        self._button(toolbar, "Set as Communications", lambda k=kind: self._act(k, self.manager.set_communications)).grid(
            row=0, column=3, padx=4, pady=6
        )
        self._button(toolbar, "Set Both", lambda k=kind: self._act(k, self.manager.set_both)).grid(row=0, column=4, padx=4, pady=6)

        tree = ttk.Treeview(
            parent,
            columns=("device", "role"),
            show="headings",
            selectmode="browse",
            style="Device.Treeview",
        )
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

    def refresh_all(self) -> None:
        self.refresh(DeviceKind.PLAYBACK)
        self.refresh(DeviceKind.RECORDING)

    def refresh(self, kind: DeviceKind) -> None:
        try:
            if self.show_all_vars[kind].get():
                self.devices[kind] = self.manager.get_all_devices(kind)
            else:
                self.devices[kind] = self.manager.get_preferred_devices(kind)
            self._update_tree_heading(kind)
            self._render_devices(kind)
            label = "all" if self.show_all_vars[kind].get() else "saved"
            self._set_status(f"Loaded {len(self.devices[kind])} {label} {kind.value} devices.")
            self._warn_missing_devices(kind)
        except Exception as exc:
            LOGGER.exception("Failed to refresh %s devices", kind.value)
            self._set_status(f"Error: {exc}")

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
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(device_text, role_text or "-"),
                tags=(tag,),
            )

        if not self.devices[kind] and not self.show_all_vars[kind].get():
            tree.insert(
                "",
                "end",
                iid="empty",
                values=("저장된 장치가 없습니다. 'Show All Devices'에서 더블클릭해 저장하세요.", "-"),
                tags=("missing",),
            )

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
        if not button:
            return
        text = "Show Saved Devices Only" if self.show_all_vars[kind].get() else "Show All Devices"
        if ctk and hasattr(button, "configure"):
            button.configure(text=text)
        elif hasattr(button, "config"):
            button.config(text=text)

    def _update_tree_heading(self, kind: DeviceKind) -> None:
        tree = self.treeviews.get(kind)
        if not tree:
            return
        tree.heading("role", text="Saved" if self.show_all_vars[kind].get() else "Current Role")

    def _warn_missing_devices(self, kind: DeviceKind) -> None:
        for device in self.devices[kind]:
            if not device.is_missing or device.id in self.missing_warned:
                continue
            self.missing_warned.add(device.id)
            message = MISSING_WARNING.format(device_name=device.name)
            self._set_status(message)
            messagebox.showwarning("Saved Device Missing", message)

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
        if index >= len(self.devices[kind]):
            return None
        return self.devices[kind][index]

    def _act(self, kind: DeviceKind, action: Callable[[AudioDevice], None]) -> None:
        device = self.selected.get(kind)
        if not device:
            self._set_status("Select a device first.")
            return
        if getattr(action, "__name__", "") == "set_both":
            self._set_both_device(kind, device, success_prefix="Set Both")
            return
        self._apply_device_action(kind, device, action, success_message=f"Applied setting to {device.name}")

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
            messagebox.showwarning("Saved Device Missing", message)
            return
        try:
            action(device)
            self._remember(kind, device)
            self.refresh(kind)
            self._set_status(success_message)
        except Exception as exc:
            LOGGER.exception("Failed to apply setting to %s", device.name)
            self._set_status(f"Error: {exc}")

    def _remember(self, kind: DeviceKind, device: AudioDevice) -> None:
        upsert_preferred_device(self.config, kind.value, device.id, device.name)
        save_config(self.config)

    def _toggle_saved_device(self, kind: DeviceKind, device: AudioDevice) -> None:
        if self.manager.is_saved_device(device):
            removed = self.manager.remove_saved_device(device)
            if removed:
                save_config(self.config)
                self.refresh(kind)
                self._set_status(f"저장된 장치에서 제거되었습니다: {device.name}")
            return

        self.manager.save_device(device)
        save_config(self.config)
        self.refresh(kind)
        self._set_status(f"장치가 저장되었습니다: {device.name}")

    def _schedule_auto_refresh(self) -> None:
        if self.auto_refresh_var.get():
            self.refresh_all()
        self.root.after(AUTO_REFRESH_MS, self._schedule_auto_refresh)

    def _on_auto_refresh_changed(self) -> None:
        self.config.auto_refresh = bool(self.auto_refresh_var.get())
        save_config(self.config)

    def _apply_initial_startup_preference(self) -> None:
        try:
            if self.config.start_with_windows and not is_startup_enabled():
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

    def _tray_role_submenu_item(self, label: str, role: str) -> pystray.MenuItem:
        submenu = pystray.Menu(lambda r=role: self._create_role_submenu(r))
        try:
            return pystray.MenuItem(label, submenu, submenu=True)
        except TypeError:
            return pystray.MenuItem(label, submenu)

    def _create_role_submenu(self, role: str) -> list[pystray.MenuItem]:
        """Return a lightweight submenu of saved devices for one tray role.

        This method reads only config-backed preferred devices. It intentionally
        avoids full pycaw enumeration so right-click and hover remain fast.
        """

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

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def hide_controller(self) -> None:
        self._save_geometry()
        self.root.withdraw()
        self._set_status("컨트롤러 창이 숨겨졌습니다. 트레이에서 다시 열 수 있습니다.")

    def exit_application(self) -> None:
        self._save_geometry()
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
