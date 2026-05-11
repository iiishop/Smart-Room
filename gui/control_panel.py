"""Popup control panel for individual device control (brightness, toggle, etc.)."""

from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

logger = logging.getLogger(__name__)


class ControlPanel(tk.Toplevel):
    """Popup window with domain-specific controls for a Home Assistant device.

    - light: brightness slider (0-255) with debounce, Turn On/Off buttons
    - switch: Toggle button
    - sensor: read-only state display (no controls)
    """

    _DEBOUNCE_MS = 200
    _BRIGHTNESS_MIN = 0
    _BRIGHTNESS_MAX = 255

    def __init__(
        self,
        parent: tk.Widget,
        entity_id: str,
        device: Any,
        device_manager: Any,
        io_loop: asyncio.AbstractEventLoop | None = None,
    ):
        super().__init__(parent)
        self._entity_id = entity_id
        self._device = device
        self._device_manager = device_manager
        self._io_loop = io_loop
        self._debounce_id: str | None = None

        friendly_name = self._resolve_friendly_name()
        self.title(friendly_name)
        self.resizable(False, False)
        self.transient(parent)

        self._build_content()

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _resolve_friendly_name(self) -> str:
        name = getattr(self._device, "name", None)
        if name is not None:
            return str(name)
        name = getattr(self._device, "friendly_name", None)
        if name is not None:
            return str(name)
        return self._entity_id

    def _device_domain(self) -> str:
        domain = getattr(self._device, "domain", None)
        value = getattr(domain, "value", domain)
        if value is None and "." in self._entity_id:
            return self._entity_id.split(".")[0]
        return str(value).lower() if value is not None else "unknown"

    def _device_state(self) -> str:
        return str(getattr(self._device, "state", "unknown"))

    def _device_brightness(self) -> int:
        brightness = getattr(self._device, "brightness", None)
        if brightness is None:
            attrs = getattr(self._device, "attributes", {})
            brightness = attrs.get("brightness", 0) if isinstance(attrs, dict) else 0
        try:
            return int(brightness)
        except (TypeError, ValueError):
            return 0

    def _build_content(self) -> None:
        domain = self._device_domain()
        main = ttk.Frame(self, padding=(16, 12))
        main.pack(fill=tk.BOTH, expand=True)

        info = ttk.LabelFrame(main, text="Device Info", padding=(12, 8))
        info.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(info, text=f"Entity ID: {self._entity_id}").pack(anchor="w")
        state_text = f"State: {self._device_state()}"
        ttk.Label(info, text=state_text).pack(anchor="w")
        ttk.Label(info, text=f"Domain: {domain}").pack(anchor="w")

        controls = ttk.LabelFrame(main, text="Controls", padding=(12, 8))
        controls.pack(fill=tk.BOTH, expand=True)

        if domain == "light":
            self._build_light_controls(controls)
        elif domain == "switch":
            self._build_switch_controls(controls)
        else:
            self._build_readonly_controls(controls)

        ttk.Separator(main, orient="horizontal").pack(fill=tk.X, pady=(12, 8))
        ttk.Button(main, text="Close", command=self.destroy).pack(anchor="e")

    def _build_light_controls(self, parent: ttk.Frame) -> None:
        brightness = self._device_brightness()
        self._brightness_var = tk.IntVar(value=brightness)
        self._brightness_label_var = tk.StringVar(value=f"Brightness: {brightness}")

        ttk.Label(parent, textvariable=self._brightness_label_var).pack(anchor="w", pady=(0, 4))

        slider = ttk.Scale(
            parent,
            from_=self._BRIGHTNESS_MIN,
            to=self._BRIGHTNESS_MAX,
            variable=self._brightness_var,
            orient=tk.HORIZONTAL,
            command=self._on_brightness_change,
        )
        slider.pack(fill=tk.X, pady=(0, 8))

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Turn On", command=self._on_turn_on).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Turn Off", command=self._on_turn_off).pack(side=tk.LEFT)

    def _build_switch_controls(self, parent: ttk.Frame) -> None:
        ttk.Button(parent, text="Toggle", command=self._on_toggle).pack(pady=(0, 4))

    def _build_readonly_controls(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="No controllable actions for this device type.").pack(anchor="w")

    def _on_brightness_change(self, value: Any) -> None:
        try:
            brightness = int(float(value))
        except (TypeError, ValueError):
            return
        self._brightness_label_var.set(f"Brightness: {brightness}")

        if self._debounce_id is not None:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(self._DEBOUNCE_MS, lambda: self._send_brightness(brightness))

    def _send_brightness(self, brightness: int) -> None:
        self._debounce_id = None
        self._invoke_service("turn_on", {"brightness": brightness})

    def _on_turn_on(self) -> None:
        self._invoke_service("turn_on")

    def _on_turn_off(self) -> None:
        self._invoke_service("turn_off")

    def _on_toggle(self) -> None:
        self._invoke_service("toggle")

    def _invoke_service(self, service: str, service_data: dict | None = None) -> None:
        dm = self._device_manager
        call_service = getattr(dm, "call_service", None)
        if callable(call_service):
            self._call_async_method(call_service, self._entity_id, service, service_data)
            return

        messagebox.showinfo(
            "Not available",
            f"Service '{service}' is not available for {self._entity_id}.",
        )

    def _call_async_method(self, method: Any, *args: Any) -> None:
        def runner() -> None:
            try:
                result = method(*args)
                if asyncio.iscoroutine(result):
                    if self._io_loop is None:
                        self.after(
                            0,
                            lambda: messagebox.showwarning(
                                "Missing event loop",
                                "Async control needs a shared event loop.",
                            ),
                        )
                        result.close()
                        return
                    future = asyncio.run_coroutine_threadsafe(result, self._io_loop)
                    future.result(timeout=10)
            except Exception as exc:
                logger.exception("Failed to call service for entity=%s", self._entity_id)
                self.after(
                    0,
                    lambda e=exc: messagebox.showerror(
                        "Control failed",
                        f"Failed to execute action: {e}",
                    ),
                )

        threading.Thread(target=runner, daemon=True).start()
