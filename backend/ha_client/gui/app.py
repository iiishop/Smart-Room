from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ha_client.config.settings import HAConfig


class HADebugApp:
    def __init__(self, config: HAConfig) -> None:
        self._config = config
        self._root = tk.Tk()
        self._root.title("Smart Room HA Client")
        self._root.geometry("720x240")

        frame = ttk.Frame(self._root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="HA Client", font=("Segoe UI", 16, "bold")).pack(
            anchor="w"
        )
        ttk.Label(frame, text=f"URL: {config.url}").pack(anchor="w", pady=(12, 0))
        ttk.Label(frame, text=f"WS: {config.ws_url}").pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Token: set" if config.token else "Token: missing"
            ),
        ).pack(anchor="w")

    def run(self) -> None:
        self._root.mainloop()
