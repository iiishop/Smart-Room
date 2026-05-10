from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any

from ...core.event_bus import EventBus, EventType

logger = logging.getLogger(__name__)

LOG_LEVEL_COLORS: dict[str, str] = {
    "INFO": "#1565c0",
    "WARN": "#e65100",
    "WARNING": "#e65100",
    "ERROR": "#c62828",
    "DEBUG": "#6a1b9a",
}


class LogPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        event_bus: EventBus,
        max_lines: int = 500,
    ) -> None:
        super().__init__(parent)
        self._event_bus = event_bus
        self._max_lines = max_lines
        self._line_count = 0

        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=2, pady=1)

        ttk.Label(toolbar, text="Log").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Clear", width=6, command=self._clear).pack(
            side=tk.RIGHT, padx=(2, 0)
        )
        self._auto_scroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="Auto",
            variable=self._auto_scroll_var,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        text_frame = ttk.Frame(self)
        text_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=6,
            pady=4,
            state=tk.DISABLED,
        )
        self._text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._text.yview)

        self._text.tag_configure(
            "INFO", foreground="#4fc3f7"
        )
        self._text.tag_configure(
            "WARN", foreground="#ffb74d"
        )
        self._text.tag_configure(
            "ERROR", foreground="#ef5350"
        )
        self._text.tag_configure(
            "CONNECT", foreground="#81c784"
        )
        self._text.tag_configure(
            "SYSTEM", foreground="#ce93d8"
        )

    def _bind_events(self) -> None:
        self._event_bus.subscribe_sync(EventType.ERROR, self._on_error)
        self._event_bus.subscribe_sync(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe_sync(EventType.CONNECTED, self._on_connected)
        self._event_bus.subscribe_sync(EventType.DISCONNECTED, self._on_disconnected)
        self._event_bus.subscribe_sync(EventType.LOG_MESSAGE, self._on_log_message)

    def _append(self, level: str, message: str) -> None:
        self._text.config(state=tk.NORMAL)

        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"

        if level == "SYSTEM":
            tag = "SYSTEM"
        elif level in ("ERROR",):
            tag = "ERROR"
        elif level in ("WARN", "WARNING"):
            tag = "WARN"
        elif level == "CONNECT":
            tag = "CONNECT"
        else:
            tag = "INFO"

        self._text.insert(tk.END, line, (tag,))
        self._line_count += 1

        while self._line_count > self._max_lines:
            self._text.delete("1.0", "2.0")
            self._line_count -= 1

        self._text.config(state=tk.DISABLED)

        if self._auto_scroll_var.get():
            self._text.see(tk.END)

    def _clear(self) -> None:
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._line_count = 0
        self._text.config(state=tk.DISABLED)

    def append_info(self, message: str) -> None:
        self._append("INFO", message)

    def append_warn(self, message: str) -> None:
        self._append("WARN", message)

    def append_error(self, message: str) -> None:
        self._append("ERROR", message)

    def _on_error(self, **data: Any) -> None:
        message = data.get("message", "unknown error")
        self._append("ERROR", f"[Error] {message}")

    def _on_state_changed(self, **data: Any) -> None:
        entity_id = data.get("entity_id", "")
        state = data.get("state", "")
        self._append("INFO", f"{entity_id} \u2192 {state}")

    def _on_connected(self, **data: Any) -> None:
        url = data.get("url", "")
        self._append("CONNECT", f"Connected to Home Assistant ({url})")

    def _on_disconnected(self, **data: Any) -> None:
        self._append("WARN", "Disconnected from Home Assistant")

    def _on_log_message(self, **data: Any) -> None:
        level = data.get("level", "INFO")
        message = data.get("message", "")
        self._append(level, message)
