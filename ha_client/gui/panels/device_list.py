from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional

from ...core.device_manager import DeviceManager
from ...core.event_bus import EventBus, EventType

DOMAIN_ICONS: dict[str, str] = {
    "light": "\U0001F315",
    "switch": "\U0001F50C",
    "sensor": "\U0001F321",
    "binary_sensor": "\U0001F4E1",
    "climate": "\u2744",
    "cover": "\U0001F6AA",
    "fan": "\U0001F4A8",
    "lock": "\U0001F512",
    "media_player": "\U0001F4FA",
    "camera": "\U0001F4F7",
    "vacuum": "\U0001F9F9",
    "automation": "\u2699",
    "scene": "\U0001F3AC",
    "script": "\U0001F4DD",
    "device_tracker": "\U0001F4CD",
    "person": "\U0001F464",
    "sun": "\u2600",
    "weather": "\u26C5",
    "zone": "\U0001F4CC",
}


class DeviceListPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        device_manager: DeviceManager,
        event_bus: EventBus,
        on_device_selected: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self._device_manager = device_manager
        self._event_bus = event_bus
        self._on_device_selected = on_device_selected

        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=2, pady=2)

        self._filter_var = tk.StringVar(value="")
        ttk.Label(toolbar, text="\U0001F50D").pack(side=tk.LEFT, padx=(0, 2))
        filter_entry = ttk.Entry(toolbar, textvariable=self._filter_var, width=20)
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        filter_entry.bind("<KeyRelease>", lambda e: self._apply_filter())

        ttk.Button(toolbar, text="\u267B", width=3, command=self.refresh).pack(
            side=tk.RIGHT, padx=(2, 0)
        )

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=("state",),
            displaycolumns=(),
            show="tree",
            yscrollcommand=scrollbar.set,
        )
        self._tree.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._tree.yview)

        self._tree.tag_configure("online", foreground="#2e7d32")
        self._tree.tag_configure("offline", foreground="#9e9e9e")
        self._tree.tag_configure("domain", font=("TkDefaultFont", 9, "bold"))
        self._tree.tag_configure("unavailable", foreground="#c62828")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _bind_events(self) -> None:
        self._event_bus.subscribe_sync(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe_sync(EventType.CONNECTED, lambda **kw: self.refresh())
        self._event_bus.subscribe_sync(EventType.DISCONNECTED, lambda **kw: self.refresh())

    def refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._domain_nodes: dict[str, str] = {}

        domains = self._device_manager.get_domains()
        if not domains:
            self._tree.insert("", tk.END, values=("\u2014 no devices \u2014",), tags=("offline",))
            return

        for domain in domains:
            icon = DOMAIN_ICONS.get(domain, "\U0001F4E6")
            domain_node = self._tree.insert(
                "",
                tk.END,
                text=f"{icon}  {domain} ({len(self._device_manager.get_domain_devices(domain))})",
                open=True,
                tags=("domain",),
            )
            self._domain_nodes[domain] = domain_node

            for entity_id in self._device_manager.get_domain_devices(domain):
                device = self._device_manager.get_device(entity_id)
                state = device.get("state", "unknown") if device else "unknown"
                friendly_name = (
                    device.get("attributes", {}).get("friendly_name", entity_id)
                    if device
                    else entity_id
                )

                state_tag = "online" if state not in ("unavailable", "unknown") else "unavailable"
                self._tree.insert(
                    domain_node,
                    tk.END,
                    iid=entity_id,
                    text=f"{friendly_name}",
                    values=(state,),
                    tags=(state_tag,),
                )

        self._apply_filter()

    def _apply_filter(self) -> None:
        filter_text = self._filter_var.get().strip().lower()
        if not filter_text:
            for item in self._tree.get_children():
                self._tree.item(item, open=True)
                for child in self._tree.get_children(item):
                    self._tree.reattach(child, item, tk.END)
            return

        for domain_node in self._tree.get_children():
            has_visible = False
            for child in self._tree.get_children(domain_node):
                item_text = self._tree.item(child, "text").lower()
                if filter_text in item_text:
                    self._tree.reattach(child, domain_node, tk.END)
                    has_visible = True
                else:
                    self._tree.detach(child)

            if has_visible:
                self._tree.item(domain_node, open=True)
            else:
                self._tree.item(domain_node, open=False)

    def _on_tree_select(self, event: Any) -> None:
        selection = self._tree.selection()
        if not selection:
            return

        item = selection[0]
        if item in self._domain_nodes.values():
            return

        self._on_device_selected(item)

    def _on_state_changed(self, **data: Any) -> None:
        entity_id = data.get("entity_id", "")
        state = data.get("state", "")
        attributes = data.get("attributes", {})

        if not self._tree.exists(entity_id):
            return

        friendly_name = attributes.get("friendly_name", entity_id)
        state_tag = "online" if state not in ("unavailable", "unknown") else "unavailable"
        self._tree.item(entity_id, text=friendly_name, values=(state,), tags=(state_tag,))

    def select_device(self, entity_id: str) -> None:
        if self._tree.exists(entity_id):
            self._tree.selection_set(entity_id)
            self._tree.see(entity_id)
