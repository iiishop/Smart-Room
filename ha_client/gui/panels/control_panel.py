import tkinter as tk
from tkinter import ttk

from ha_client.core.controller import DeviceController
from ha_client.gui.async_helper import AsyncTkHelper
from ha_client.models.device import Device, Light, Switch, Sensor


class ControlPanel:
    def __init__(
        self,
        parent: ttk.Frame,
        controller: DeviceController,
        async_helper: AsyncTkHelper,
    ):
        self._controller = controller
        self._async_helper = async_helper
        self._current_device: Device | None = None

        self._frame = ttk.LabelFrame(parent, text="Device Details & Control", padding=4)
        self._frame.pack(fill=tk.BOTH, expand=True)

        self._info_frame = ttk.LabelFrame(self._frame, text="Info", padding=4)
        self._info_frame.pack(fill=tk.X, pady=(0, 8))

        info_grid = ttk.Frame(self._info_frame)
        info_grid.pack(fill=tk.X)

        ttk.Label(info_grid, text="Entity ID:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self._lbl_entity_id = ttk.Label(info_grid, text="-")
        self._lbl_entity_id.grid(row=0, column=1, sticky=tk.W)

        ttk.Label(info_grid, text="Domain:").grid(row=1, column=0, sticky=tk.W, padx=(0, 8))
        self._lbl_domain = ttk.Label(info_grid, text="-")
        self._lbl_domain.grid(row=1, column=1, sticky=tk.W)

        ttk.Label(info_grid, text="State:").grid(row=2, column=0, sticky=tk.W, padx=(0, 8))
        self._lbl_state = ttk.Label(info_grid, text="-")
        self._lbl_state.grid(row=2, column=1, sticky=tk.W)

        self._control_frame = ttk.LabelFrame(self._frame, text="Controls", padding=4)
        self._control_frame.pack(fill=tk.BOTH, expand=True)

        self._light_controls = ttk.Frame(self._control_frame)
        self._switch_controls = ttk.Frame(self._control_frame)
        self._sensor_controls = ttk.Frame(self._control_frame)
        self._no_device_label = ttk.Label(
            self._control_frame,
            text="Select a device from the list to view controls",
            foreground="gray",
        )
        self._no_device_label.pack(expand=True)

    def show_device(self, device: Device):
        self._current_device = device
        self._lbl_entity_id.config(text=device.entity_id)
        self._lbl_domain.config(text=device.domain.value)
        self._lbl_state.config(text=device.state)

        self._hide_all_controls()

        if isinstance(device, Light):
            self._build_light_controls(device)
            self._light_controls.pack(fill=tk.BOTH, expand=True)
        elif isinstance(device, Switch):
            self._build_switch_controls(device)
            self._switch_controls.pack(fill=tk.BOTH, expand=True)
        elif isinstance(device, Sensor):
            self._build_sensor_controls(device)
            self._sensor_controls.pack(fill=tk.BOTH, expand=True)
        else:
            self._build_generic_controls(device)

    def _hide_all_controls(self):
        self._no_device_label.pack_forget()
        self._light_controls.pack_forget()
        self._switch_controls.pack_forget()
        self._sensor_controls.pack_forget()

    def _clear_frame(self, frame: ttk.Frame):
        for widget in frame.winfo_children():
            widget.destroy()

    def _build_light_controls(self, light: Light):
        self._clear_frame(self._light_controls)

        btn_frame = ttk.Frame(self._light_controls)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        entity_id = light.entity_id

        ttk.Button(
            btn_frame,
            text="Turn On",
            command=lambda: self._async_helper.run_task(
                self._controller.turn_on(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            btn_frame,
            text="Turn Off",
            command=lambda: self._async_helper.run_task(
                self._controller.turn_off(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            btn_frame,
            text="Toggle",
            command=lambda: self._async_helper.run_task(
                self._controller.toggle(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=2)

        bright_frame = ttk.LabelFrame(self._light_controls, text="Brightness", padding=4)
        bright_frame.pack(fill=tk.X, pady=(0, 8))

        self._brightness_var = tk.IntVar(value=light.brightness or 0)
        bright_slider = ttk.Scale(
            bright_frame,
            from_=0,
            to=255,
            orient=tk.HORIZONTAL,
            variable=self._brightness_var,
            command=lambda v: self._on_brightness_change(entity_id),
        )
        bright_slider.pack(fill=tk.X)

        self._brightness_label = ttk.Label(
            bright_frame, text=f"{self._brightness_var.get()} / 255"
        )
        self._brightness_label.pack()

        color_frame = ttk.LabelFrame(self._light_controls, text="Color", padding=4)
        color_frame.pack(fill=tk.X)

        rgb = light.rgb_color or (255, 255, 255)

        r_frame = ttk.Frame(color_frame)
        r_frame.pack(fill=tk.X, pady=1)
        ttk.Label(r_frame, text="R:").pack(side=tk.LEFT, padx=(0, 4))
        self._r_var = tk.IntVar(value=rgb[0])
        ttk.Scale(
            r_frame, from_=0, to=255, orient=tk.HORIZONTAL, variable=self._r_var,
            command=lambda v: self._on_color_change(entity_id),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._r_label = ttk.Label(r_frame, text=str(rgb[0]), width=4)
        self._r_label.pack(side=tk.RIGHT)

        g_frame = ttk.Frame(color_frame)
        g_frame.pack(fill=tk.X, pady=1)
        ttk.Label(g_frame, text="G:").pack(side=tk.LEFT, padx=(0, 4))
        self._g_var = tk.IntVar(value=rgb[1])
        ttk.Scale(
            g_frame, from_=0, to=255, orient=tk.HORIZONTAL, variable=self._g_var,
            command=lambda v: self._on_color_change(entity_id),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._g_label = ttk.Label(g_frame, text=str(rgb[1]), width=4)
        self._g_label.pack(side=tk.RIGHT)

        b_frame = ttk.Frame(color_frame)
        b_frame.pack(fill=tk.X, pady=1)
        ttk.Label(b_frame, text="B:").pack(side=tk.LEFT, padx=(0, 4))
        self._b_var = tk.IntVar(value=rgb[2])
        ttk.Scale(
            b_frame, from_=0, to=255, orient=tk.HORIZONTAL, variable=self._b_var,
            command=lambda v: self._on_color_change(entity_id),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._b_label = ttk.Label(b_frame, text=str(rgb[2]), width=4)
        self._b_label.pack(side=tk.RIGHT)

    def _build_switch_controls(self, switch: Switch):
        self._clear_frame(self._switch_controls)

        entity_id = switch.entity_id
        state_text = "ON" if switch.is_on else "OFF"

        state_label = ttk.Label(
            self._switch_controls,
            text=f"Switch is {state_text}",
            font=("", 12, "bold"),
        )
        state_label.pack(pady=10)

        btn_frame = ttk.Frame(self._switch_controls)
        btn_frame.pack()

        ttk.Button(
            btn_frame,
            text="Turn On",
            command=lambda: self._async_helper.run_task(
                self._controller.turn_on(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btn_frame,
            text="Turn Off",
            command=lambda: self._async_helper.run_task(
                self._controller.turn_off(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            btn_frame,
            text="Toggle",
            command=lambda: self._async_helper.run_task(
                self._controller.toggle(entity_id)
            ),
        ).pack(side=tk.LEFT, padx=4)

    def _build_sensor_controls(self, sensor: Sensor):
        self._clear_frame(self._sensor_controls)

        info_frame = ttk.Frame(self._sensor_controls)
        info_frame.pack(expand=True, pady=20)

        ttk.Label(
            info_frame,
            text=f"Value: {sensor.state}",
            font=("", 14),
        ).pack()

        if sensor.unit:
            ttk.Label(
                info_frame,
                text=f"Unit: {sensor.unit}",
            ).pack(pady=(4, 0))

        if sensor.device_class:
            ttk.Label(
                info_frame,
                text=f"Class: {sensor.device_class}",
            ).pack(pady=(2, 0))

        ttk.Label(
            info_frame,
            text="(Read-only sensor)",
            foreground="gray",
        ).pack(pady=(8, 0))

    def _build_generic_controls(self, device: Device):
        self._no_device_label.config(
            text=f"Device: {device.name}\nDomain: {device.domain.value}\nNo specific controls available"
        )
        self._no_device_label.pack(expand=True)

    def _on_brightness_change(self, entity_id: str):
        val = self._brightness_var.get()
        self._brightness_label.config(text=f"{val} / 255")
        self._async_helper.run_task(
            self._controller.set_brightness(entity_id, val)
        )

    def _on_color_change(self, entity_id: str):
        self._r_label.config(text=str(self._r_var.get()))
        self._g_label.config(text=str(self._g_var.get()))
        self._b_label.config(text=str(self._b_var.get()))
