from __future__ import annotations

import asyncio
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..config import HAConfig
from ..core.device_controller import DeviceController
from ..core.device_manager import DeviceManager
from ..core.event_bus import EventBus, EventType
from ..core.ha_api import HAClient
from .async_helper import AsyncTkHelper
from .panels.control_panel import ControlPanel
from .panels.device_list import DeviceListPanel
from .panels.log_panel import LogPanel, LogPanelHandler

logger = logging.getLogger(__name__)


class HADebugApp:
    def __init__(self, config: HAConfig) -> None:
        self._config = config

        self._event_bus = EventBus()
        self._ha_client = HAClient(config, self._event_bus)
        self._device_controller = DeviceController(config, self._event_bus, self._ha_client)

        self._root = tk.Tk()
        self._root.title("Home Assistant Debug Panel")
        self._root.geometry("1024x700")
        self._root.minsize(800, 500)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._async_helper = AsyncTkHelper(self._root)

        self._device_manager = DeviceManager(
            config, self._event_bus, self._ha_client, self._async_helper.loop
        )

        self._build_ui()
        self._bind_events()

        self._async_helper.start()

        self._root.after(500, self._connect_ha)

    def run(self) -> None:
        self._root.mainloop()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")

        main_paned = ttk.PanedWindow(self._root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

        self._device_list_panel = DeviceListPanel(
            main_paned,
            self._device_manager,
            self._event_bus,
            on_device_selected=self._on_device_selected,
        )
        main_paned.add(self._device_list_panel, weight=40)

        self._control_panel = ControlPanel(
            main_paned,
            self._device_manager,
            self._device_controller,
            self._async_helper,
        )
        main_paned.add(self._control_panel, weight=60)

        log_separator = ttk.Separator(self._root, orient=tk.HORIZONTAL)
        log_separator.pack(fill=tk.X, padx=4, pady=(4, 0))

        self._log_panel = LogPanel(
            self._root,
            self._event_bus,
            max_lines=self._config.log_max_lines,
        )
        self._log_panel.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._setup_logging_handler()

        self._log_panel.append_info("Application started. Connecting to Home Assistant...")

        self._status_frame = ttk.Frame(self._root, relief=tk.SUNKEN, padding=(8, 2))
        self._status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_led = tk.Label(
            self._status_frame,
            text="\u25CF",
            fg="red",
            font=("TkDefaultFont", 10),
        )
        self._status_led.pack(side=tk.LEFT, padx=(0, 4))

        self._status_text = tk.StringVar(value="Disconnected")
        ttk.Label(self._status_frame, textvariable=self._status_text).pack(side=tk.LEFT)

        self._status_sep1 = ttk.Separator(self._status_frame, orient=tk.VERTICAL)
        self._status_sep1.pack(side=tk.LEFT, padx=8, fill=tk.Y)

        self._device_count_var = tk.StringVar(value="Devices: 0")
        ttk.Label(self._status_frame, textvariable=self._device_count_var).pack(side=tk.LEFT)

        self._status_sep2 = ttk.Separator(self._status_frame, orient=tk.VERTICAL)
        self._status_sep2.pack(side=tk.LEFT, padx=8, fill=tk.Y)

        self._ha_url_var = tk.StringVar(value=self._config.url)
        ttk.Label(self._status_frame, textvariable=self._ha_url_var).pack(side=tk.LEFT)

    def _setup_logging_handler(self) -> None:
        handler = LogPanelHandler(self._log_panel)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

    def _bind_events(self) -> None:
        self._event_bus.subscribe_sync(EventType.CONNECTED, self._update_status_connected)
        self._event_bus.subscribe_sync(EventType.DISCONNECTED, self._update_status_disconnected)
        self._event_bus.subscribe_sync(EventType.STATE_CHANGED, self._on_state_changed_sync)

    def _connect_ha(self) -> None:
        self._async_helper.run_task(self._ha_client.connect())

    def _on_device_selected(self, entity_id: str) -> None:
        self._control_panel.show_device(entity_id)

    def _update_status_connected(self, **data: Any) -> None:
        self._root.after(0, self._set_status_connected)

    def _set_status_connected(self) -> None:
        self._status_led.config(fg="#4caf50")
        self._status_text.set("Connected")
        self._device_count_var.set(
            f"Devices: {len(self._device_manager.devices)}"
        )

    def _update_status_disconnected(self, **data: Any) -> None:
        self._root.after(0, self._set_status_disconnected)

    def _set_status_disconnected(self) -> None:
        self._status_led.config(fg="red")
        self._status_text.set("Disconnected")
        self._device_count_var.set("Devices: 0")

    def _on_state_changed_sync(self, **data: Any) -> None:
        self._root.after(
            0,
            lambda: self._device_count_var.set(
                f"Devices: {len(self._device_manager.devices)}"
            ),
        )
        self._root.after(0, self._control_panel.refresh_current)

    def _on_close(self) -> None:
        self._log_panel.append_info("Shutting down...")

        async def _cleanup() -> None:
            await self._ha_client.disconnect()

        self._async_helper.run_task(_cleanup())
        self._async_helper.stop()
        self._root.destroy()
