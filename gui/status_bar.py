"""Bottom status bar with connection indicator, device count, and last-update time."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone
from typing import Optional


class StatusBar(ttk.Frame):
    """Bottom status bar component.

    - Left: connection status indicator (colored circle + label)
      - green = connected, gray = disconnected, yellow = connecting
    - Middle: "Devices: N"
    - Right: "Last update: HH:MM:SS"
    """

    _STATUS_COLORS: dict[str, str] = {
        "connected": "#2ECC40",
        "disconnected": "#AAAAAA",
        "connecting": "#FFDC00",
    }

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, relief=tk.SUNKEN, padding=(8, 4))
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)

        self._indicator_label = ttk.Label(self, text="", font=("TkDefaultFont", 10, "bold"))
        self._indicator_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._status_text = ttk.Label(self, text="Disconnected")
        self._status_text.grid(row=0, column=0, sticky="w", padx=(24, 0))

        self._device_count_var = tk.StringVar(value="Devices: 0")
        device_label = ttk.Label(self, textvariable=self._device_count_var)
        device_label.grid(row=0, column=1)

        self._last_update_var = tk.StringVar(value="Last update: --:--:--")
        update_label = ttk.Label(self, textvariable=self._last_update_var)
        update_label.grid(row=0, column=2, sticky="e")

        self._current_status: str = "disconnected"

    def set_connection_status(self, status: str) -> None:
        """Update connection status indicator.

        status: "connected" | "disconnected" | "connecting"
        """
        self._current_status = status
        color = self._STATUS_COLORS.get(status, "#AAAAAA")
        self._indicator_label.configure(text="\u25cf", foreground=color)
        self._status_text.configure(text=status.capitalize())

    def set_device_count(self, count: int) -> None:
        self._device_count_var.set(f"Devices: {count}")

    def set_last_update(self, timestamp: Optional[str] = None) -> None:
        """Parse ISO 8601 timestamp and display as HH:MM:SS."""
        if timestamp is None:
            self._last_update_var.set("Last update: --:--:--")
            return

        try:
            dt = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            self._last_update_var.set("Last update: --:--:--")
            return

        local_dt = dt.astimezone()
        formatted = local_dt.strftime("%H:%M:%S")
        self._last_update_var.set(f"Last update: {formatted}")
