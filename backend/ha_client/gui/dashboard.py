"""DashboardApp - main application window integrating all dashboard components."""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from typing import Any

from ha_client.api.connection import ConnectionManager
from ha_client.config.settings import HAConfig
from ha_client.core.controller import DeviceController
from ha_client.core.device_manager import DeviceManager
from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.gui.card_grid import CardGridView
from ha_client.gui.sidebar import SidebarFrame
from ha_client.gui.status_bar import StatusBar

logger = logging.getLogger(__name__)


class DashboardApp:
    """Main Smart Room Dashboard application window."""

    _DEFAULT_TITLE = "Smart Room Dashboard"
    _DEFAULT_WIDTH = 1280
    _DEFAULT_HEIGHT = 800
    _MIN_WIDTH = 900
    _MIN_HEIGHT = 600

    def __init__(
        self,
        config: HAConfig,
        connection_mgr: ConnectionManager,
        device_manager: DeviceManager,
        controller: DeviceController,
        event_bus: EventBus,
    ):
        self._config = config
        self._conn_mgr = connection_mgr
        self._device_mgr = device_manager
        self._controller = controller
        self._event_bus = event_bus

        self._root = tk.Tk()
        self._root.title(self._DEFAULT_TITLE)
        self._root.geometry(f"{self._DEFAULT_WIDTH}x{self._DEFAULT_HEIGHT}")
        self._root.minsize(self._MIN_WIDTH, self._MIN_HEIGHT)
        self._center_window()

        self._bridge = AsyncTkBridge(self._root)

        self._build_menu()
        self._build_layout()
        self._bind_events()

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self._root.mainloop()

    def _center_window(self) -> None:
        self._root.update_idletasks()
        w = self._root.winfo_width()
        h = self._root.winfo_height()
        x = (self._root.winfo_screenwidth() // 2) - (w // 2)
        y = (self._root.winfo_screenheight() // 2) - (h // 2)
        self._root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self._root)
        self._root.config(menu=menu_bar)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Connect", command=self._on_connect)
        file_menu.add_command(label="Disconnect", command=self._on_disconnect)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)

        config_menu = tk.Menu(menu_bar, tearoff=0)
        config_menu.add_command(label="Edit Config", command=self._on_edit_config)
        menu_bar.add_cascade(label="Config", menu=config_menu)

    def _build_layout(self) -> None:
        main_pane = ttk.PanedWindow(self._root, orient="horizontal")
        main_pane.pack(fill="both", expand=True)

        self._sidebar = SidebarFrame(
            main_pane,
            self._device_mgr,
            self._event_bus,
            self._on_category_selected,
        )
        self._sidebar.set_bridge(self._bridge)
        main_pane.add(self._sidebar, weight=0)

        self._card_grid = CardGridView(
            main_pane,
            self._device_mgr,
            self._controller,
            self._bridge,
        )
        main_pane.add(self._card_grid, weight=1)

        self._status_bar = StatusBar(self._root)
        self._status_bar.pack(side="bottom", fill="x")

    def _bind_events(self) -> None:
        self._event_bus.subscribe(EventType.CONNECTION_CHANGED, self._on_connection_changed)

    def _on_category_selected(self, domain: str | None) -> None:
        self._card_grid.set_domain_filter(domain)

    def _on_connect(self) -> None:
        if self._conn_mgr.online:
            return
        self._status_bar.set_connection_status("connecting")
        self._bridge.run_async(self._start(), on_result=self._on_connect_result)

    async def _start(self) -> Any:
        try:
            await self._conn_mgr.start()
            devices = await self._device_mgr.load_devices()
            await self._device_mgr.start_sync()
            return devices
        except Exception as exc:
            logger.exception("Connection failed")
            return exc

    def _on_connect_result(self, result: Any) -> None:
        if isinstance(result, Exception):
            self._status_bar.set_connection_status("disconnected")
            messagebox.showerror(
                "Connection Failed",
                f"Could not connect to Home Assistant:\n{result}",
                parent=self._root,
            )
            return

        devices = result
        self._status_bar.set_connection_status("connected")
        self._status_bar.set_device_count(len(devices))
        self._event_bus.emit(
            EventType.CONNECTION_CHANGED,
            online=True,
            device_count=len(devices),
        )
        self._card_grid.refresh(devices)
        self._sidebar.refresh(devices)

    def _on_disconnect(self) -> None:
        if not self._conn_mgr.online:
            return
        self._status_bar.set_connection_status("disconnected")
        self._event_bus.emit(EventType.CONNECTION_CHANGED, online=False)
        self._bridge.run_async(self._stop(), on_result=self._on_disconnect_result)

    async def _stop(self) -> None:
        try:
            await self._conn_mgr.stop()
        except Exception:
            logger.exception("Error during disconnect")

    def _on_disconnect_result(self, _: Any) -> None:
        pass

    def _on_edit_config(self) -> None:
        config_path = os.environ.get("HA_CONFIG", "")
        if not config_path:
            default_path = pathlib.Path(__file__).resolve().parents[2] / "config.yaml"
            config_path = str(default_path)

        try:
            if sys.platform == "win32":
                os.startfile(config_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", config_path])
            else:
                subprocess.Popen(["xdg-open", config_path])
        except Exception:
            messagebox.showinfo(
                "Edit Config",
                f"Config file location:\n{config_path}",
                parent=self._root,
            )

    def _on_connection_changed(self, **event_data: Any) -> None:
        online = event_data.get("online", False)
        status = "connected" if online else "disconnected"
        self._status_bar.set_connection_status(status)
        self._sidebar.set_online(online)
        if online:
            count = event_data.get("device_count", 0)
            self._status_bar.set_device_count(count)

    def _on_close(self) -> None:
        def _finalize(_: Any = None) -> None:
            self._bridge.shutdown()
            try:
                self._root.destroy()
            except tk.TclError:
                pass

        if self._conn_mgr.online:
            self._bridge.run_async(self._stop(), on_result=_finalize)
        else:
            _finalize()
