import asyncio
import logging
import tkinter as tk
from tkinter import ttk

from ha_client.api.connection import ConnectionManager
from ha_client.config.settings import HAConfig
from ha_client.core.controller import DeviceController
from ha_client.core.device_manager import DeviceManager
from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_helper import AsyncTkHelper
from ha_client.gui.panels.control_panel import ControlPanel
from ha_client.gui.panels.device_list import DeviceListPanel
from ha_client.gui.panels.log_panel import LogPanel

logger = logging.getLogger(__name__)


class HADebugApp:
    def __init__(self, config: HAConfig):
        self._config = config

        self._connection_mgr = ConnectionManager(config)
        self._event_bus = EventBus()
        self._device_manager = DeviceManager(self._connection_mgr, self._event_bus)
        self._device_controller = DeviceController(self._connection_mgr, self._event_bus)

        self._root = tk.Tk()
        self._root.title("Home Assistant Debug Panel")
        self._root.geometry("1100x700")
        self._root.minsize(800, 500)

        self._async_helper = AsyncTkHelper(self._root)

        self._selected_device_id: str | None = None

        self._build_ui()
        self._connect_events()

    def _build_ui(self):
        self._root.grid_rowconfigure(0, weight=1)
        self._root.grid_columnconfigure(0, weight=2)
        self._root.grid_columnconfigure(1, weight=3)

        main_pw = ttk.PanedWindow(self._root, orient=tk.HORIZONTAL)
        main_pw.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)

        left_frame = ttk.Frame(main_pw)
        main_pw.add(left_frame, weight=2)

        right_frame = ttk.Frame(main_pw)
        main_pw.add(right_frame, weight=3)

        self._device_list_panel = DeviceListPanel(
            left_frame, self._device_manager, self._async_helper, self._on_device_selected
        )

        self._control_panel = ControlPanel(
            right_frame, self._device_controller, self._async_helper
        )

        bottom_frame = ttk.Frame(self._root)
        bottom_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(0, 2))

        self._log_panel = LogPanel(bottom_frame, self._event_bus)

        status_frame = ttk.Frame(self._root, relief=tk.SUNKEN, padding=(4, 2))
        status_frame.grid(row=2, column=0, columnspan=2, sticky="ew")

        self._status_connection = ttk.Label(status_frame, text="● Disconnected", foreground="red")
        self._status_connection.pack(side=tk.LEFT, padx=(0, 20))

        self._status_device_count = ttk.Label(status_frame, text="Devices: 0")
        self._status_device_count.pack(side=tk.LEFT, padx=(0, 20))

        self._status_url = ttk.Label(
            status_frame, text=f"HA: {self._config.url}"
        )
        self._status_url.pack(side=tk.LEFT)

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _connect_events(self):
        self._event_bus.subscribe(EventType.CONNECTION_CHANGED, self._on_connection_changed)
        self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe(EventType.ERROR, self._on_error)

    def _on_device_selected(self, entity_id: str):
        self._selected_device_id = entity_id
        device = self._device_manager.get_device(entity_id)
        if device:
            self._control_panel.show_device(device)

    def _on_connection_changed(self, **kwargs):
        connected = kwargs.get("connected", False)
        self._root.after(0, self._update_connection_status, connected)

    def _on_state_changed(self, **kwargs):
        entity_id = kwargs.get("entity_id", "")
        device = kwargs.get("device")
        self._root.after(0, self._refresh_ui_after_state_change, entity_id, device)

    def _on_error(self, **kwargs):
        message = kwargs.get("message", "Unknown error")
        logger.error(f"EventBus ERROR: {message}")

    def _update_connection_status(self, connected: bool):
        if connected:
            self._status_connection.config(text="● Connected", foreground="green")
        else:
            self._status_connection.config(text="● Disconnected", foreground="red")

    def _refresh_ui_after_state_change(self, entity_id: str, device):
        self._device_list_panel.refresh_tree()
        if device and entity_id == self._selected_device_id:
            self._control_panel.show_device(device)
        self._status_device_count.config(
            text=f"Devices: {len(self._device_manager.devices)}"
        )

    def _on_close(self):
        self._async_helper.run_task(self._shutdown())
        self._root.after(200, self._final_close)

    async def _shutdown(self):
        self._async_helper.stop()
        await self._connection_mgr.stop()

    def _final_close(self):
        self._root.destroy()

    def run(self):
        async def _startup():
            try:
                await self._connection_mgr.start()
                connected = self._connection_mgr.online
                self._event_bus.emit(
                    EventType.CONNECTION_CHANGED,
                    connected=connected,
                )
                if connected:
                    await self._device_manager.load_devices()
                    await self._device_manager.start_sync()
            except Exception as e:
                logger.error(f"Startup error: {e}")
                self._event_bus.emit(EventType.ERROR, message=str(e))

        self._async_helper.start()
        self._async_helper.run_task(_startup())

        self._root.after(500, self._post_startup_refresh)
        self._root.mainloop()

    def _post_startup_refresh(self):
        self._device_list_panel.refresh_tree()
        self._status_device_count.config(
            text=f"Devices: {len(self._device_manager.devices)}"
        )

        online = self._connection_mgr.online
        self._update_connection_status(online)
        self._root.after(5000, self._post_startup_refresh)
