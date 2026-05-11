"""Device list panel built with tkinter.ttk Treeview."""

from __future__ import annotations

import asyncio
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any


class DeviceListFrame(tk.Frame):
    """Treeview panel with filtering and basic device control buttons."""

    _DOMAIN_OPTIONS = ("All", "light", "switch", "sensor", "binary_sensor")
    _DOMAIN_TAGS = {
        "light": {"foreground": "#D9822B"},
        "switch": {"foreground": "#106BA3"},
        "sensor": {"foreground": "#5C7080"},
        "binary_sensor": {"foreground": "#0F9960"},
        "unknown": {"foreground": "#8A9BA8"},
    }
    _DOMAIN_MARK = {
        "light": "LGT",
        "switch": "SW",
        "sensor": "SNS",
        "binary_sensor": "BIN",
        "unknown": "UNK",
    }

    def __init__(self, parent: tk.Widget, device_manager: Any, io_loop: asyncio.AbstractEventLoop | None = None):
        super().__init__(parent)
        self._device_manager = device_manager
        self._io_loop = io_loop
        self._all_devices: list[Any] = []
        self._selected_entity_id: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_filter_bar()
        self._build_tree()
        self._build_controls()
        self._set_control_enabled(False)
        self._show_connecting_placeholder()

    def _build_filter_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        bar.grid_columnconfigure(3, weight=1)

        ttk.Label(bar, text="Domain:").grid(row=0, column=0, sticky="w")
        self._domain_var = tk.StringVar(value="All")
        self._domain_combo = ttk.Combobox(
            bar,
            textvariable=self._domain_var,
            values=list(self._DOMAIN_OPTIONS),
            state="readonly",
            width=14,
        )
        self._domain_combo.grid(row=0, column=1, sticky="w", padx=(6, 12))
        self._domain_combo.bind("<<ComboboxSelected>>", lambda _e: self._filter_and_refresh())

        ttk.Label(bar, text="Search:").grid(row=0, column=2, sticky="w")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_args: self._filter_and_refresh())
        self._search_entry = ttk.Entry(bar, textvariable=self._search_var)
        self._search_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))

    def _build_tree(self) -> None:
        tree_wrap = ttk.Frame(self)
        tree_wrap.grid(row=1, column=0, sticky="nsew", padx=8)
        tree_wrap.grid_columnconfigure(0, weight=1)
        tree_wrap.grid_rowconfigure(0, weight=1)

        columns = ("entity_id", "friendly_name", "state", "domain")
        self._tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        self._tree.grid(row=0, column=0, sticky="nsew")

        vscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self._tree.configure(yscrollcommand=vscroll.set)

        self._tree.heading("entity_id", text="Entity ID")
        self._tree.heading("friendly_name", text="Friendly Name")
        self._tree.heading("state", text="State")
        self._tree.heading("domain", text="Domain")

        self._tree.column("entity_id", width=240, anchor="w")
        self._tree.column("friendly_name", width=220, anchor="w")
        self._tree.column("state", width=90, anchor="center")
        self._tree.column("domain", width=120, anchor="center")

        for tag, style in self._DOMAIN_TAGS.items():
            self._tree.tag_configure(tag, **style)

        self._tree.bind("<<TreeviewSelect>>", self._on_device_selected)

    def _build_controls(self) -> None:
        controls = ttk.Frame(self)
        controls.grid(row=2, column=0, sticky="ew", padx=8, pady=(6, 8))
        controls.grid_columnconfigure(4, weight=1)

        self._btn_on = ttk.Button(controls, text="Turn On", command=self._on_turn_on)
        self._btn_off = ttk.Button(controls, text="Turn Off", command=self._on_turn_off)
        self._btn_toggle = ttk.Button(controls, text="Toggle", command=self._on_toggle)
        self._btn_dimmer = ttk.Button(controls, text="Dimmer", command=self._on_dimmer)

        self._btn_on.grid(row=0, column=0, padx=(0, 6))
        self._btn_off.grid(row=0, column=1, padx=(0, 6))
        self._btn_toggle.grid(row=0, column=2, padx=(0, 6))
        self._btn_dimmer.grid(row=0, column=3)

    def _show_connecting_placeholder(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._tree.insert(
            "",
            "end",
            iid="__connecting__",
            values=("-", "Connecting...", "-", "-"),
            tags=("unknown",),
        )

    def refresh(self, devices: list[Any]) -> None:
        """Refresh device list rows in Treeview (main thread only)."""
        self._all_devices = list(devices)
        self._filter_and_refresh()

    def _on_device_selected(self, _event: tk.Event) -> None:
        selected = self._tree.selection()
        if not selected:
            self._selected_entity_id = None
            self._set_control_enabled(False)
            return

        entity_id = selected[0]
        if entity_id.startswith("__"):
            self._selected_entity_id = None
            self._set_control_enabled(False)
            return

        self._selected_entity_id = entity_id
        self._set_control_enabled(True)

    def _filter_and_refresh(self) -> None:
        domain_filter = self._domain_var.get().strip().lower()
        query = self._search_var.get().strip().lower()

        visible = []
        for device in self._all_devices:
            domain_name = self._device_domain(device)
            if domain_filter and domain_filter != "all" and domain_name != domain_filter:
                continue
            if query:
                haystack = f"{getattr(device, 'entity_id', '')} {getattr(device, 'name', '')}".lower()
                if query not in haystack:
                    continue
            visible.append(device)

        self._tree.delete(*self._tree.get_children())
        self._selected_entity_id = None
        self._set_control_enabled(False)

        if not visible:
            self._tree.insert(
                "",
                "end",
                iid="__empty__",
                values=("-", "No devices", "-", "-"),
                tags=("unknown",),
            )
            return

        visible.sort(key=lambda d: str(getattr(d, "entity_id", "")))
        for device in visible:
            entity_id = str(getattr(device, "entity_id", ""))
            friendly_name = str(getattr(device, "name", entity_id))
            state = str(getattr(device, "state", "unknown"))
            domain_name = self._device_domain(device)
            mark = self._DOMAIN_MARK.get(domain_name, "UNK")
            domain_text = f"{mark} {domain_name}"
            self._tree.insert(
                "",
                "end",
                iid=entity_id,
                values=(entity_id, friendly_name, state, domain_text),
                tags=(domain_name if domain_name in self._DOMAIN_TAGS else "unknown",),
            )

    def _set_control_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self._btn_on.configure(state=state)
        self._btn_off.configure(state=state)
        self._btn_toggle.configure(state=state)
        self._btn_dimmer.configure(state=state)

    def _on_turn_on(self) -> None:
        self._invoke_control("turn_on")

    def _on_turn_off(self) -> None:
        self._invoke_control("turn_off")

    def _on_toggle(self) -> None:
        self._invoke_control("toggle")

    def _on_dimmer(self) -> None:
        self._invoke_control("set_brightness", 128)

    def _invoke_control(self, method_name: str, *args: Any) -> None:
        entity_id = self._selected_entity_id
        if not entity_id:
            return

        method = getattr(self._device_manager, method_name, None)
        if callable(method):
            self._call_method_threadsafe(method, entity_id, *args)
            return

        conn_mgr = getattr(self._device_manager, "connection_mgr", None)
        rest = getattr(conn_mgr, "rest", None) if conn_mgr is not None else None
        rest_method = getattr(rest, method_name, None) if rest is not None else None
        if callable(rest_method):
            self._call_method_threadsafe(rest_method, entity_id, *args)
            return

        messagebox.showinfo(
            "Not implemented",
            f"Control '{method_name}' is not available yet for {entity_id}.",
        )

    def _call_method_threadsafe(self, method: Any, *args: Any) -> None:
        def runner() -> None:
            try:
                result = method(*args)
                if asyncio.iscoroutine(result):
                    if self._io_loop is None:
                        self.after(
                            0,
                            lambda: messagebox.showwarning(
                                "Missing event loop",
                                "Async control needs a shared event loop and is not wired yet.",
                            ),
                        )
                        result.close()
                        return
                    future = asyncio.run_coroutine_threadsafe(result, self._io_loop)
                    future.result(timeout=10)
            except Exception as exc:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Control failed",
                        f"Failed to run control action: {exc}",
                    ),
                )

        threading.Thread(target=runner, daemon=True).start()

    @staticmethod
    def _device_domain(device: Any) -> str:
        domain = getattr(device, "domain", None)
        value = getattr(domain, "value", domain)
        if value is None:
            return "unknown"
        return str(value).lower()
