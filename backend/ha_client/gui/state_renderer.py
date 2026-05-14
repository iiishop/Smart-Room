from __future__ import annotations

import tkinter as tk
from datetime import datetime, timezone
from typing import Any

EXCLUDED_ATTRIBUTE_KEYS = {
    "entity_id", "supported_features", "icon", "attribution",
}


class StateRenderer:
    @staticmethod
    def get_display_attributes(attributes: dict) -> dict:
        return {
            k: v for k, v in attributes.items()
            if k not in EXCLUDED_ATTRIBUTE_KEYS
        }

    @staticmethod
    def render_state(
        parent: tk.Widget,
        entity_id: str,
        state: str,
        attributes: dict,
    ) -> list[tk.Widget]:
        widgets: list[tk.Widget] = []

        status_label = StateRenderer._render_status_indicator(parent, state)
        widgets.append(status_label)

        display_attrs = StateRenderer.get_display_attributes(attributes)

        for key, value in display_attrs.items():
            if key == "friendly_name":
                continue

            widget = StateRenderer._render_attribute(parent, key, value)
            if widget is not None:
                widgets.append(widget)

        return widgets

    @staticmethod
    def _render_status_indicator(parent: tk.Widget, state: str) -> tk.Label:
        color_map = {"on": "#4CAF50", "off": "#9E9E9E", "unavailable": "#F44336"}
        color = color_map.get(state, "#9E9E9E")

        frame = tk.Frame(parent, bg=parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0")
        canvas = tk.Canvas(frame, width=18, height=18, highlightthickness=0, bg=frame.cget("bg"))
        canvas.create_oval(3, 3, 15, 15, fill=color, outline="")
        canvas.pack(side=tk.LEFT, padx=(0, 6))

        label = tk.Label(frame, text=state.upper(), font=("Segoe UI", 12, "bold"), fg=color, bg=frame.cget("bg"))
        label.pack(side=tk.LEFT)

        return frame

    @staticmethod
    def _render_attribute(parent: tk.Widget, key: str, value: Any) -> tk.Widget | None:
        if isinstance(value, bool):
            return StateRenderer._render_bool_indicator(parent, key, value)

        if isinstance(value, (int, float)):
            if any(kw in key.lower() for kw in ("brightness", "temperature", "humidity", "power", "current", "voltage", "battery", "pressure", "speed")):
                return StateRenderer._render_progress_bar(parent, key, float(value))

        if isinstance(value, (tuple, list)) and len(value) == 3:
            if all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in value):
                return StateRenderer._render_color_block(parent, key, value)

        if isinstance(value, str):
            if key in ("last_changed", "last_updated"):
                return StateRenderer._render_time_ago(parent, key, value)
            if "unit" in key.lower():
                return StateRenderer._render_key_value(parent, key, str(value))

        return StateRenderer._render_key_value(parent, key, str(value))

    @staticmethod
    def _render_bool_indicator(parent: tk.Widget, key: str, value: bool) -> tk.Frame:
        bg = parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0"
        frame = tk.Frame(parent, bg=bg)

        color = "#4CAF50" if value else "#9E9E9E"
        canvas = tk.Canvas(frame, width=12, height=12, highlightthickness=0, bg=bg)
        canvas.create_oval(2, 2, 10, 10, fill=color, outline="")
        canvas.pack(side=tk.LEFT, padx=(0, 4))

        display_name = key.replace("_", " ").title()
        label = tk.Label(frame, text=f"{display_name}: {'Yes' if value else 'No'}", font=("Segoe UI", 9), fg="#333333", bg=bg)
        label.pack(side=tk.LEFT)

        return frame

    @staticmethod
    def _render_progress_bar(parent: tk.Widget, key: str, value: float) -> tk.Frame:
        bg = parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0"
        frame = tk.Frame(parent, bg=bg)

        display_name = key.replace("_", " ").title()
        label = tk.Label(frame, text=display_name, font=("Segoe UI", 9), fg="#555555", bg=bg)
        label.pack(anchor=tk.W, pady=(0, 2))

        max_val = 100.0
        if "brightness" in key.lower():
            max_val = 255.0
        elif "temperature" in key.lower():
            max_val = 100.0
        elif "battery" in key.lower():
            max_val = 100.0
        elif "speed" in key.lower():
            max_val = 100.0
        elif value > max_val:
            max_val = value * 1.2

        ratio = min(1.0, max(0.0, value / max_val))
        bar_width = 200
        bar_height = 12

        canvas = tk.Canvas(frame, width=bar_width, height=bar_height, highlightthickness=0, bg="#e0e0e0")
        canvas.create_rectangle(0, 0, bar_width * ratio, bar_height, fill="#2196F3", outline="")
        canvas.pack(side=tk.LEFT, padx=(0, 6))

        val_label = tk.Label(frame, text=f"{value:.1f}", font=("Segoe UI", 9, "bold"), fg="#333333", bg=bg)
        val_label.pack(side=tk.LEFT)

        return frame

    @staticmethod
    def _render_color_block(parent: tk.Widget, key: str, rgb: tuple | list) -> tk.Frame:
        bg = parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0"
        frame = tk.Frame(parent, bg=bg)

        display_name = key.replace("_", " ").title()
        label = tk.Label(frame, text=display_name, font=("Segoe UI", 9), fg="#555555", bg=bg)
        label.pack(side=tk.LEFT, padx=(0, 6))

        r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        block = tk.Canvas(frame, width=24, height=24, highlightthickness=0, bg=bg)
        block.create_rectangle(2, 2, 22, 22, fill=hex_color, outline="#cccccc")
        block.pack(side=tk.LEFT, padx=(0, 6))

        hex_label = tk.Label(frame, text=hex_color.upper(), font=("Segoe UI", 9), fg="#888888", bg=bg)
        hex_label.pack(side=tk.LEFT)

        return frame

    @staticmethod
    def _render_time_ago(parent: tk.Widget, key: str, iso_time: str) -> tk.Frame:
        bg = parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0"
        frame = tk.Frame(parent, bg=bg)

        display_name = key.replace("_", " ").title()
        ago_text = StateRenderer._format_time_ago(iso_time)

        label = tk.Label(frame, text=f"{display_name}: {ago_text}", font=("Segoe UI", 8), fg="#888888", bg=bg)
        label.pack(anchor=tk.W)

        return frame

    @staticmethod
    def _render_key_value(parent: tk.Widget, key: str, value: str) -> tk.Frame:
        bg = parent.cget("bg") if parent.cget("bg") != "SystemButtonFace" else "#f0f0f0"
        frame = tk.Frame(parent, bg=bg)

        display_name = key.replace("_", " ").title()
        label = tk.Label(frame, text=f"{display_name}: {value}", font=("Segoe UI", 9), fg="#555555", bg=bg)
        label.pack(anchor=tk.W)

        return frame

    @staticmethod
    def _format_time_ago(iso_time: str) -> str:
        try:
            dt = datetime.fromisoformat(iso_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = now - dt
            seconds = int(delta.total_seconds())

            if seconds < 0:
                return "just now"
            if seconds < 60:
                return f"{seconds}s ago"
            if seconds < 3600:
                return f"{seconds // 60}m ago"
            if seconds < 86400:
                return f"{seconds // 3600}h ago"
            return f"{seconds // 86400}d ago"
        except (ValueError, TypeError):
            return iso_time[:19] if iso_time and len(iso_time) >= 19 else str(iso_time)
