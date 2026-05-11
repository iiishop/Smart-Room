from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Optional

from ...core.device_controller import DeviceController
from ...core.device_manager import DeviceManager
from ...gui.async_helper import AsyncTkHelper

logger = logging.getLogger(__name__)


class ControlPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        device_manager: DeviceManager,
        device_controller: DeviceController,
        async_helper: AsyncTkHelper,
    ) -> None:
        super().__init__(parent)
        self._device_manager = device_manager
        self._device_controller = device_controller
        self._async_helper = async_helper
        self._current_entity_id: Optional[str] = None
        self._control_widgets: list[tk.Widget] = []

        self._header_frame = ttk.Frame(self)
        self._header_frame.pack(fill=tk.X, padx=8, pady=(8, 0))

        self._content_frame = ttk.Frame(self)
        self._content_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._show_placeholder()

    def show_device(self, entity_id: str) -> None:
        self._current_entity_id = entity_id
        self._clear_controls()

        device = self._device_manager.get_device(entity_id)
        if not device:
            self._show_placeholder()
            return

        domain = entity_id.split(".", 1)[0]

        for widget in self._header_frame.winfo_children():
            widget.destroy()

        state = device.get("state", "unknown")
        attrs = device.get("attributes", {})
        friendly_name = attrs.get("friendly_name", entity_id)

        ttk.Label(
            self._header_frame,
            text=friendly_name,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor=tk.W)

        info_frame = ttk.Frame(self._header_frame)
        info_frame.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(info_frame, text=f"Entity ID: {entity_id}", foreground="#666").pack(anchor=tk.W)
        ttk.Label(info_frame, text=f"State: {state}", foreground="#666").pack(anchor=tk.W)

        if domain == "light":
            self._build_light_controls(entity_id, device)
        elif domain == "switch":
            self._build_switch_controls(entity_id, device)
        elif domain in ("sensor", "binary_sensor"):
            self._build_sensor_display(device)
        else:
            self._build_generic_display(device)

    def refresh_current(self) -> None:
        if self._current_entity_id:
            self.show_device(self._current_entity_id)

    def _clear_controls(self) -> None:
        for widget in self._content_frame.winfo_children():
            widget.destroy()

    def _show_placeholder(self) -> None:
        for widget in self._header_frame.winfo_children():
            widget.destroy()
        self._clear_controls()
        ttk.Label(
            self._content_frame,
            text="\u2190 Select a device from the list",
            foreground="#999",
            font=("TkDefaultFont", 10),
        ).pack(expand=True)

    def _build_light_controls(self, entity_id: str, device: dict[str, Any]) -> None:
        attrs = device.get("attributes", {})
        supported_features = attrs.get("supported_features", 0)
        state = device.get("state", "off")
        brightness = attrs.get("brightness", 0) if attrs.get("brightness") is not None else 0
        rgb_color = attrs.get("rgb_color")
        color_temp = attrs.get("color_temp")

        btn_frame = ttk.Frame(self._content_frame)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        if state == "on":
            ttk.Button(
                btn_frame,
                text="\u23F9 Turn Off",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.light_turn_off(entity_id)
                ),
            ).pack(side=tk.LEFT, padx=(0, 4))

            ttk.Button(
                btn_frame,
                text="\U0001F504 Toggle",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.light_toggle(entity_id)
                ),
            ).pack(side=tk.LEFT)
        else:
            ttk.Button(
                btn_frame,
                text="\U0001F4A1 Turn On",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.light_turn_on(entity_id)
                ),
            ).pack(side=tk.LEFT, padx=(0, 4))

            ttk.Button(
                btn_frame,
                text="\U0001F504 Toggle",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.light_toggle(entity_id)
                ),
            ).pack(side=tk.LEFT)

        brightness_pct = int(brightness * 100 / 255)
        brightness_frame = ttk.LabelFrame(self._content_frame, text="Brightness", padding=8)
        brightness_frame.pack(fill=tk.X, pady=(8, 0))

        self._brightness_var = tk.IntVar(value=brightness_pct)
        self._brightness_label = ttk.Label(
            brightness_frame, text=f"{brightness_pct}%", width=5, anchor=tk.CENTER
        )
        self._brightness_label.pack(anchor=tk.N, pady=(0, 4))

        brightness_slider = ttk.Scale(
            brightness_frame,
            from_=0,
            to=100,
            variable=self._brightness_var,
            orient=tk.HORIZONTAL,
            command=lambda v: self._on_brightness_change(entity_id, float(v)),
        )
        brightness_slider.pack(fill=tk.X)

        if rgb_color:
            color_frame = ttk.LabelFrame(self._content_frame, text="Color", padding=8)
            color_frame.pack(fill=tk.X, pady=(8, 0))

            color_row = ttk.Frame(color_frame)
            color_row.pack(fill=tk.X)

            r, g, b = int(rgb_color[0]), int(rgb_color[1]), int(rgb_color[2])
            hex_color = f"#{r:02x}{g:02x}{b:02x}"

            self._color_preview = tk.Frame(color_row, bg=hex_color, width=32, height=32, relief=tk.SUNKEN, bd=1)
            self._color_preview.pack(side=tk.LEFT, padx=(0, 8))

            self._color_var = tk.StringVar(value=hex_color.upper())
            tk.Label(color_row, textvariable=self._color_var, font=("TkFixedFont", 10)).pack(side=tk.LEFT)

    def _build_switch_controls(self, entity_id: str, device: dict[str, Any]) -> None:
        state = device.get("state", "off")

        btn_frame = ttk.Frame(self._content_frame)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        if state == "on":
            ttk.Button(
                btn_frame,
                text="\u23F9 Turn Off",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.switch_turn_off(entity_id)
                ),
            ).pack(side=tk.LEFT, padx=(0, 4))
        else:
            ttk.Button(
                btn_frame,
                text="\U0001F50C Turn On",
                command=lambda: self._async_helper.run_task(
                    self._device_controller.switch_turn_on(entity_id)
                ),
            ).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            btn_frame,
            text="\U0001F504 Toggle",
            command=lambda: self._async_helper.run_task(
                self._device_controller.switch_toggle(entity_id)
            ),
        ).pack(side=tk.LEFT)

    def _build_sensor_display(self, device: dict[str, Any]) -> None:
        attrs = device.get("attributes", {})
        state = device.get("state", "unknown")
        unit = attrs.get("unit_of_measurement", "")

        value_frame = ttk.Frame(self._content_frame)
        value_frame.pack(expand=True)

        ttk.Label(
            value_frame,
            text=f"{state} {unit}".strip(),
            font=("TkDefaultFont", 18, "bold"),
        ).pack()

        ttk.Label(
            value_frame,
            text="Read-only sensor",
            foreground="#999",
        ).pack(pady=(4, 0))

        history_frame = ttk.LabelFrame(self._content_frame, text="History (placeholder)", padding=8)
        history_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        ttk.Label(
            history_frame,
            text="Historical trend data will appear here\nwhen backend support is enabled.",
            foreground="#999",
            justify=tk.CENTER,
        ).pack(expand=True)

    def _build_generic_display(self, device: dict[str, Any]) -> None:
        attrs = device.get("attributes", {})
        state = device.get("state", "unknown")

        info_text = f"State: {state}\n\nAttributes:"
        for key, value in attrs.items():
            if key in ("friendly_name", "icon", "entity_picture"):
                continue
            info_text += f"\n  {key}: {value}"

        text_widget = tk.Text(
            self._content_frame,
            wrap=tk.WORD,
            height=10,
            bg="#f5f5f5",
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert("1.0", info_text)
        text_widget.config(state=tk.DISABLED)

    def _on_brightness_change(self, entity_id: str, value: float) -> None:
        pct = int(value)
        self._brightness_label.config(text=f"{pct}%")
        self._async_helper.run_task(
            self._device_controller.light_set_brightness(entity_id, pct)
        )
