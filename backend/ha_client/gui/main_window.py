"""Main window container for tkinter-based HA debug client."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any

from ha_client.core.event_bus import EventType

from ha_client.gui.device_list import DeviceListFrame
from ha_client.gui.status_bar import StatusBar


class MainWindow:
    """Builds the main shell window and wires device update listeners."""

    def __init__(self, root: tk.Tk, device_manager: Any):
        self.root = root
        self.device_manager = device_manager

        self.root.title("HA Client Debug")
        self.root.geometry("900x600")
        self.root.minsize(760, 500)

        self._create_menu()
        self._create_layout()
        self._bind_device_events()
        self._refresh_devices_on_ui_thread()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Connect", command=self._on_connect)
        file_menu.add_command(label="Disconnect", command=self._on_disconnect)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)

        config_menu = tk.Menu(menubar, tearoff=False)
        config_menu.add_command(label="Edit Config", command=self._on_edit_config)

        menubar.add_cascade(label="File", menu=file_menu)
        menubar.add_cascade(label="Config", menu=config_menu)
        self.root.config(menu=menubar)

    def _create_layout(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        io_loop = self._resolve_io_loop()
        self.device_list_frame = DeviceListFrame(container, self.device_manager, io_loop=io_loop)
        self.device_list_frame.grid(row=0, column=0, sticky="nsew")

        self.status_bar = StatusBar(container)
        self.status_bar.grid(row=1, column=0, sticky="ew")

    def _bind_device_events(self) -> None:
        event_bus = getattr(self.device_manager, "event_bus", None)
        if event_bus is None:
            return

        event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        event_bus.subscribe(EventType.DEVICE_ADDED, self._on_device_added)

    def _on_state_changed(self, **_event_data: Any) -> None:
        """Schedules a safe UI refresh via root.after(0, ...)."""
        self.root.after(0, self._refresh_devices_on_ui_thread)

    def _on_device_added(self, **_event_data: Any) -> None:
        self.root.after(0, self._refresh_devices_on_ui_thread)

    def _refresh_devices_on_ui_thread(self) -> None:
        devices_map = getattr(self.device_manager, "devices", {})
        devices = list(devices_map.values()) if isinstance(devices_map, dict) else list(devices_map)
        self.device_list_frame.refresh(devices)
        self.status_bar.set_device_count(len(devices))
        self.status_bar.set_last_update(datetime.now(timezone.utc).isoformat())

    def _on_connect(self) -> None:
        self.status_bar.set_connection_status("connecting")

    def _on_disconnect(self) -> None:
        self.status_bar.set_connection_status("disconnected")

    def _on_edit_config(self) -> None:
        messagebox.showinfo("Config", "Config editor is not implemented yet.")

    def _on_close(self) -> None:
        self.root.destroy()

    def _resolve_io_loop(self):
        conn_mgr = getattr(self.device_manager, "connection_mgr", None)
        ws = getattr(conn_mgr, "ws", None) if conn_mgr is not None else None
        for attr in ("loop", "_loop"):
            loop = getattr(ws, attr, None)
            if loop is not None:
                return loop
        return None
