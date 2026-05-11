import tkinter as tk
from tkinter import ttk
from datetime import datetime

from ha_client.core.event_bus import EventBus, EventType

MAX_LOG_LINES = 500


class LogPanel:
    def __init__(self, parent: ttk.Frame, event_bus: EventBus):
        self._event_bus = event_bus

        frame = ttk.LabelFrame(parent, text="Log", padding=4)
        frame.pack(fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            frame,
            height=6,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9),
        )
        self._text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.configure(yscrollcommand=scrollbar.set)

        self._text.tag_configure("info", foreground="black")
        self._text.tag_configure("state_change", foreground="blue")
        self._text.tag_configure("connection", foreground="green")
        self._text.tag_configure("error", foreground="red")
        self._text.tag_configure("timestamp", foreground="gray")

        self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe(EventType.ERROR, self._on_error)
        self._event_bus.subscribe(EventType.CONNECTION_CHANGED, self._on_connection)
        self._event_bus.subscribe(EventType.DEVICE_ADDED, self._on_device_added)
        self._event_bus.subscribe(EventType.DEVICE_REMOVED, self._on_device_removed)

    def _on_state_changed(self, **kwargs):
        entity_id = kwargs.get("entity_id", "unknown")
        device = kwargs.get("device")
        state = device.state if device else "unknown"
        self._log(f"{entity_id} -> {state}", "state_change")

    def _on_error(self, **kwargs):
        message = kwargs.get("message", "Unknown error")
        self._log(f"ERROR: {message}", "error")

    def _on_connection(self, **kwargs):
        connected = kwargs.get("connected", False)
        status = "connected" if connected else "disconnected"
        self._log(f"Connection {status}", "connection")

    def _on_device_added(self, **kwargs):
        entity_id = kwargs.get("entity_id", "unknown")
        self._log(f"Device added: {entity_id}", "info")

    def _on_device_removed(self, **kwargs):
        entity_id = kwargs.get("entity_id", "unknown")
        self._log(f"Device removed: {entity_id}", "info")

    def _log(self, message: str, tag: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")

        self._text.configure(state=tk.NORMAL)

        self._text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self._text.insert(tk.END, f"{message}\n", tag)

        total_lines = int(self._text.index("end-1c").split(".")[0])
        if total_lines > MAX_LOG_LINES:
            self._text.delete("1.0", f"{total_lines - MAX_LOG_LINES + 50}.0")

        self._text.see(tk.END)
        self._text.configure(state=tk.DISABLED)
