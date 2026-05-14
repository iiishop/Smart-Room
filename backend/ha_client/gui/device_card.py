"""Device card widget — per-device card integrating state display and controls."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DOMAIN_ICONS: dict[str, str] = {
    "light": "\U0001f4a1",
    "switch": "\U0001f50c",
    "sensor": "\U0001f4ca",
    "binary_sensor": "\U0001f6a8",
    "climate": "\U0001f321\ufe0f",
    "cover": "\U0001f6aa",
    "media_player": "\U0001f4fa",
    "fan": "\U0001f4a8",
    "lock": "\U0001f512",
    "scene": "\U0001f3ac",
    "automation": "\U0001f916",
    "script": "\U0001f4dc",
    "unknown": "\U00002753",
}

_CARD_WIDTH = 280
_SELECTED_COLOR = "#2196F3"
_FLASH_COLOR = "#FFD54F"
_FLASH_DURATION_MS = 600


class DeviceCard(tk.Frame):
    """Single-device card showing domain icon, name, state, controls, and entity_id."""

    def __init__(
        self,
        parent: tk.Widget,
        entity_id: str,
        domain: str,
        state: str,
        attributes: dict[str, Any],
        supported_features: set[int],
        available_services: dict[str, dict[str, Any]],
        on_action: Callable[[str, str, Any], None],
        on_card_click: Callable[[str], None],
        bridge: Any = None,
    ):
        super().__init__(
            parent,
            width=_CARD_WIDTH,
            borderwidth=1,
            relief=tk.FLAT,
            highlightthickness=0,
        )
        self._entity_id = entity_id
        self._domain = domain
        self._state = state
        self._attributes = dict(attributes)
        self._supported_features = set(supported_features)
        self._available_services = dict(available_services)
        self._on_action = on_action
        self._on_card_click = on_card_click
        self._bridge = bridge
        self._selected = False
        self._flash_id: str | None = None

        self._friendly_name = self._attributes.get("friendly_name", entity_id)

        self.pack_propagate(False)
        self.configure(width=_CARD_WIDTH)

        self._build_title_bar()
        self._build_state_area()
        self._build_controls_area()
        self._build_footer()

        self.bind("<Button-1>", self._handle_click)
        self._bind_children_recursive(self, "<Button-1>", self._handle_click)

    def _bind_children_recursive(self, widget: tk.Widget, sequence: str, handler: Callable) -> None:
        for child in widget.winfo_children():
            child.bind(sequence, handler)
            self._bind_children_recursive(child, sequence, handler)

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg="#F5F5F5", height=32)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        icon = _DOMAIN_ICONS.get(self._domain, _DOMAIN_ICONS["unknown"])
        self._title_label = tk.Label(
            bar,
            text=f"{icon} {self._friendly_name}",
            bg="#F5F5F5",
            fg="#333333",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=8,
        )
        self._title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_state_area(self) -> None:
        self._state_frame = tk.Frame(self, bg="#FFFFFF", padx=8, pady=4)
        self._state_frame.pack(fill=tk.X)

        self._state_indicator = tk.Label(
            self._state_frame,
            text=f"\u25cf {self._state.upper()}",
            bg="#FFFFFF",
            fg=self._state_color(),
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self._state_indicator.pack(anchor="w")

        self._detail_label = tk.Label(
            self._state_frame,
            text=self._build_detail_text(),
            bg="#FFFFFF",
            fg="#666666",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
        )
        self._detail_label.pack(anchor="w", fill=tk.X)

    def _state_color(self) -> str:
        s = self._state.lower()
        if s in ("on", "open", "playing", "unlocked", "active", "home"):
            return "#2ECC40"
        elif s in ("off", "closed", "paused", "idle", "locked"):
            return "#AAAAAA"
        elif s in ("unavailable",):
            return "#E74C3C"
        else:
            return "#FFDC00"

    def _build_detail_text(self) -> str:
        lines: list[str] = []
        attrs = self._attributes

        if self._domain == "light":
            brightness = attrs.get("brightness")
            if brightness is not None:
                pct = round(brightness / 2.55)
                bar = self._progress_bar(pct)
                lines.append(f"Brightness: {pct}%")
                lines.append(bar)
            ct = attrs.get("color_temp")
            if ct is not None:
                lines.append(f"Color Temp: {ct} mired")

        elif self._domain == "climate":
            current = attrs.get("current_temperature")
            target = attrs.get("temperature")
            if current is not None:
                lines.append(f"Current: {current}\u00b0")
            if target is not None:
                lines.append(f"Target: {target}\u00b0")
            hvac = attrs.get("hvac_action", "")
            if hvac:
                lines.append(f"Action: {hvac}")

        elif self._domain in ("sensor", "binary_sensor"):
            unit = attrs.get("unit_of_measurement", "")
            val = self._state
            if unit:
                val += f" {unit}"
            lines.append(val)
            dev_class = attrs.get("device_class", "")
            if dev_class:
                lines.append(f"Class: {dev_class}")

        elif self._domain == "media_player":
            media_title = attrs.get("media_title", "")
            if media_title:
                lines.append(media_title)
            volume = attrs.get("volume_level")
            if volume is not None:
                pct = round(volume * 100)
                lines.append(f"Volume: {pct}%")

        return "\n".join(lines) if lines else ""

    @staticmethod
    def _progress_bar(pct: float) -> str:
        block = "\u2588"
        shade = "\u2591"
        filled = round(pct / 10)
        empty = 10 - filled
        return f"[{block * filled}{shade * empty}]"

    def _build_controls_area(self) -> None:
        self._controls_frame = tk.Frame(self, bg="#FFFFFF", padx=8, pady=4)
        self._controls_frame.pack(fill=tk.X)

        domain = self._domain

        if domain == "light":
            self._build_light_controls()
        elif domain == "switch":
            self._build_switch_controls()
        elif domain == "climate":
            self._build_climate_controls()
        elif domain == "media_player":
            self._build_media_controls()
        elif domain == "cover":
            self._build_cover_controls()
        elif domain == "lock":
            self._build_lock_controls()
        elif domain == "fan":
            self._build_binary_controls("Turn On", "Turn Off")
        else:
            tk.Label(
                self._controls_frame,
                text="No controls",
                bg="#FFFFFF",
                fg="#AAAAAA",
                font=("Segoe UI", 8),
            ).pack(anchor="w")

    def _build_light_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [
            ("Turn On", "turn_on"),
            ("Turn Off", "turn_off"),
            ("Toggle", "toggle"),
        ]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action: self._on_action(self._entity_id, a, None),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_switch_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [
            ("Turn On", "turn_on"),
            ("Turn Off", "turn_off"),
            ("Toggle", "toggle"),
        ]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action: self._on_action(self._entity_id, a, None),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_climate_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [
            ("Heat", "set_hvac_mode"),
            ("Cool", "set_hvac_mode"),
            ("Auto", "set_hvac_mode"),
            ("Off", "set_hvac_mode"),
        ]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action, v=text.lower(): self._on_action(self._entity_id, a, {"hvac_mode": v}),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_media_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [
            ("Play", "media_play"),
            ("Pause", "media_pause"),
            ("Stop", "media_stop"),
        ]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action: self._on_action(self._entity_id, a, None),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_cover_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [
            ("Open", "open_cover"),
            ("Close", "close_cover"),
            ("Stop", "stop_cover"),
        ]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action: self._on_action(self._entity_id, a, None),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_lock_controls(self) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        for text, action in [("Lock", "lock"), ("Unlock", "unlock")]:
            tk.Button(
                btn_frame,
                text=text,
                font=("Segoe UI", 8),
                command=lambda a=action: self._on_action(self._entity_id, a, None),
            ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_binary_controls(self, on_text: str, off_text: str) -> None:
        btn_frame = tk.Frame(self._controls_frame, bg="#FFFFFF")
        btn_frame.pack(fill=tk.X)

        tk.Button(
            btn_frame,
            text=on_text,
            font=("Segoe UI", 8),
            command=lambda: self._on_action(self._entity_id, "turn_on", None),
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_frame,
            text=off_text,
            font=("Segoe UI", 8),
            command=lambda: self._on_action(self._entity_id, "turn_off", None),
        ).pack(side=tk.LEFT, padx=(0, 4))

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg="#F5F5F5", height=20)
        footer.pack(fill=tk.X)
        footer.pack_propagate(False)

        tk.Label(
            footer,
            text=self._entity_id,
            bg="#F5F5F5",
            fg="#AAAAAA",
            font=("Consolas", 7),
            anchor="w",
            padx=8,
        ).pack(anchor="w")

    def _handle_click(self, _event: tk.Event | None = None) -> None:
        self._on_card_click(self._entity_id)

    def update_state(self, state: str, attributes: dict[str, Any]) -> None:
        old_state = self._state
        self._state = state
        self._attributes = dict(attributes)

        self._state_indicator.configure(
            text=f"\u25cf {self._state.upper()}",
            fg=self._state_color(),
        )

        self._detail_label.configure(text=self._build_detail_text())

        if old_state != state:
            self._animate_flash()

    def _animate_flash(self) -> None:
        if self._flash_id is not None:
            self.after_cancel(self._flash_id)
            self._flash_id = None

        original_bg = self.cget("bg")
        self.configure(bg=_FLASH_COLOR)
        self._state_frame.configure(bg=_FLASH_COLOR)
        self._controls_frame.configure(bg=_FLASH_COLOR)

        def _restore() -> None:
            self.configure(bg=original_bg)
            self._state_frame.configure(bg="#FFFFFF")
            self._controls_frame.configure(bg="#FFFFFF")
            self._flash_id = None

        self._flash_id = self.after(_FLASH_DURATION_MS, _restore)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        color = _SELECTED_COLOR if selected else "#CCCCCC"
        width = 2 if selected else 1
        self.configure(
            highlightbackground=color,
            highlightcolor=color,
            highlightthickness=width,
        )

    @staticmethod
    def get_domain_icon(domain: str) -> str:
        return _DOMAIN_ICONS.get(domain, _DOMAIN_ICONS["unknown"])
