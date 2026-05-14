"""DeviceCard - individual device card widget with state display and controls."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from ha_client.models.device import Device, Light, Switch, Sensor
from ha_client.models.entity import EntityDomain


class StateRenderer:
    """Renders device state into visual indicators."""

    @staticmethod
    def render(parent: tk.Widget, device: Any) -> None:
        for child in parent.winfo_children():
            child.destroy()

        state = str(getattr(device, "state", "unknown"))
        domain = StateRenderer._resolve_domain(device)

        if domain in ("light", "switch"):
            on = state == "on"
            indicator = ttk.Label(
                parent,
                text="\u25cf ON" if on else "\u25cb OFF",
                foreground="#2ECC40" if on else "#AAAAAA",
                font=("TkDefaultFont", 11, "bold"),
            )
            indicator.pack(anchor="w", pady=(4, 0))

        if domain == "light":
            brightness = getattr(device, "brightness", None)
            if brightness is None:
                attrs = getattr(device, "attributes", {})
                brightness = attrs.get("brightness") if isinstance(attrs, dict) else None
            if brightness is not None:
                try:
                    pct = round(int(brightness) / 2.55)
                    bar = ttk.Progressbar(parent, length=80, mode="determinate", value=pct)
                    bar.pack(anchor="w", pady=(4, 0))
                    ttk.Label(parent, text=f"{pct}%", font=("TkDefaultFont", 9)).pack(anchor="w")
                except (TypeError, ValueError):
                    pass

        elif domain == "sensor":
            unit = StateRenderer._resolve_unit(device)
            label = f"{state}{' ' + unit if unit else ''}"
            ttk.Label(parent, text=label, font=("TkDefaultFont", 11, "bold"), foreground="#5C7080").pack(
                anchor="w", pady=(4, 0)
            )

        else:
            ttk.Label(
                parent,
                text=f"State: {state}",
                font=("TkDefaultFont", 10),
                foreground="#8A9BA8",
            ).pack(anchor="w", pady=(4, 0))

    @staticmethod
    def _resolve_domain(device: Any) -> str:
        domain = getattr(device, "domain", None)
        if isinstance(domain, EntityDomain):
            return domain.value
        if domain is not None:
            return str(domain).lower()
        return "unknown"

    @staticmethod
    def _resolve_unit(device: Any) -> str:
        unit = getattr(device, "unit_of_measurement", None)
        if unit is not None:
            return str(unit)
        attrs = getattr(device, "attributes", {})
        if isinstance(attrs, dict):
            u = attrs.get("unit_of_measurement", "")
            return str(u) if u else ""
        return ""


class WidgetFactory:
    """Creates control widgets for a device based on its domain and available services."""

    def __init__(self, services: dict | None = None):
        self._services = services or {}

    def create_controls(self, parent: tk.Widget, device: Any, on_action: Callable) -> None:
        for child in parent.winfo_children():
            child.destroy()

        domain = self._resolve_domain(device)
        entity_id = str(getattr(device, "entity_id", ""))

        if domain == "light":
            self._create_light_controls(parent, entity_id, device, on_action)
        elif domain == "switch":
            self._create_switch_controls(parent, entity_id, on_action)
        elif domain == "cover":
            self._create_cover_controls(parent, entity_id, on_action)
        elif domain == "media_player":
            self._create_media_controls(parent, entity_id, on_action)
        else:
            ttk.Label(parent, text="---", foreground="#AAAAAA").pack(pady=(4, 0))

    def _create_light_controls(self, parent, entity_id, device, on_action):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(6, 0))
        ttk.Button(frame, text="Toggle", command=lambda: on_action(entity_id, "toggle")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="On", command=lambda: on_action(entity_id, "turn_on")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Off", command=lambda: on_action(entity_id, "turn_off")).pack(side="left")

    def _create_switch_controls(self, parent, entity_id, on_action):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(6, 0))
        ttk.Button(frame, text="Toggle", command=lambda: on_action(entity_id, "toggle")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="On", command=lambda: on_action(entity_id, "turn_on")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Off", command=lambda: on_action(entity_id, "turn_off")).pack(side="left")

    def _create_cover_controls(self, parent, entity_id, on_action):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(6, 0))
        ttk.Button(frame, text="Open", command=lambda: on_action(entity_id, "open_cover")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Close", command=lambda: on_action(entity_id, "close_cover")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Stop", command=lambda: on_action(entity_id, "stop_cover")).pack(side="left")

    def _create_media_controls(self, parent, entity_id, on_action):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(6, 0))
        ttk.Button(frame, text="Play", command=lambda: on_action(entity_id, "media_play")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Pause", command=lambda: on_action(entity_id, "media_pause")).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(frame, text="Stop", command=lambda: on_action(entity_id, "media_stop")).pack(side="left")

    @staticmethod
    def _resolve_domain(device: Any) -> str:
        domain = getattr(device, "domain", None)
        if isinstance(domain, EntityDomain):
            return domain.value
        if domain is not None:
            return str(domain).lower()
        return "unknown"


class DeviceCard(tk.Frame):
    """Card widget representing a single Home Assistant device."""

    CARD_WIDTH = 200
    CARD_HEIGHT = 160
    _DOMAIN_COLORS = {
        "light": "#D9822B",
        "switch": "#106BA3",
        "sensor": "#5C7080",
        "binary_sensor": "#0F9960",
        "cover": "#8E6B23",
        "media_player": "#A82DA8",
        "climate": "#D93838",
        "unknown": "#8A9BA8",
    }

    def __init__(
        self,
        parent: tk.Widget,
        device: Any,
        on_action: Callable | None = None,
        widget_factory: WidgetFactory | None = None,
        **kw,
    ):
        super().__init__(parent, **kw)
        self._device = device
        self._on_action = on_action or (lambda eid, act: None)
        self._widget_factory = widget_factory or WidgetFactory()

        self.configure(
            width=self.CARD_WIDTH,
            height=self.CARD_HEIGHT,
            relief=tk.RAISED,
            borderwidth=1,
            bg="#FFFFFF",
        )
        self.pack_propagate(False)

        self._build_card(device)

    @property
    def device(self):
        return self._device

    @property
    def entity_id(self) -> str:
        return str(getattr(self._device, "entity_id", ""))

    def update_state(self, device: Any) -> None:
        self._device = device
        self._rebuild_state()

    def _build_card(self, device: Any) -> None:
        entity_id = str(getattr(device, "entity_id", ""))
        name = str(getattr(device, "name", entity_id))
        domain = self._resolve_domain(device)

        header_color = self._DOMAIN_COLORS.get(domain, self._DOMAIN_COLORS["unknown"])
        header = tk.Frame(self, bg=header_color, height=24)
        header.pack(fill="x")
        header.pack_propagate(False)
        domain_label = tk.Label(
            header, text=domain.upper(), bg=header_color, fg="#FFFFFF",
            font=("TkDefaultFont", 8, "bold"),
        )
        domain_label.pack(side="left", padx=6, pady=2)

        body = tk.Frame(self, bg="#FFFFFF")
        body.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        body.pack_propagate(False)

        name_label = tk.Label(
            body, text=name, bg="#FFFFFF", fg="#333333",
            font=("TkDefaultFont", 10, "bold"), anchor="w", wraplength=180,
        )
        name_label.pack(fill="x")

        eid_label = tk.Label(
            body, text=entity_id, bg="#FFFFFF", fg="#888888",
            font=("TkDefaultFont", 7), anchor="w",
        )
        eid_label.pack(fill="x", pady=(2, 0))

        self._state_frame = tk.Frame(body, bg="#FFFFFF")
        self._state_frame.pack(fill="x", pady=(4, 0))
        StateRenderer.render(self._state_frame, device)

        self._controls_frame = tk.Frame(body, bg="#FFFFFF")
        self._controls_frame.pack(fill="x", side="bottom")
        if self._widget_factory:
            self._widget_factory.create_controls(self._controls_frame, device, self._on_action)

    def _rebuild_state(self) -> None:
        StateRenderer.render(self._state_frame, self._device)

    @staticmethod
    def _resolve_domain(device: Any) -> str:
        domain = getattr(device, "domain", None)
        if isinstance(domain, EntityDomain):
            return domain.value
        if domain is not None:
            return str(domain).lower()
        return "unknown"
