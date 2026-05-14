"""SidebarFrame - domain category navigation sidebar for device dashboard."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.models.entity import EntityDomain

logger = logging.getLogger(__name__)

DOMAIN_LABELS: dict[str, str] = {
    "light": "Lights",
    "switch": "Switches",
    "sensor": "Sensors",
    "binary_sensor": "Binary Sensors",
    "climate": "Climate",
    "cover": "Covers",
    "media_player": "Media Players",
    "fan": "Fans",
    "lock": "Locks",
    "scene": "Scenes",
    "automation": "Automations",
    "script": "Scripts",
    "vacuum": "Vacuums",
    "camera": "Cameras",
    "device_tracker": "Device Trackers",
}

SORT_ORDER: dict[str, int] = {"light": 1, "switch": 2, "sensor": 3}


def resolve_domain(device: Any) -> str:
    domain = getattr(device, "domain", None)
    if hasattr(domain, "value"):
        return domain.value
    if domain is not None:
        return str(domain).lower()
    entity_id = str(getattr(device, "entity_id", ""))
    if "." in entity_id:
        return entity_id.split(".")[0]
    return "unknown"


class _CategoryItem(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        domain_key: str | None,
        icon: str,
        label: str,
        count: int,
        on_select: Callable[[str | None], None],
        **kw,
    ):
        super().__init__(parent, **kw)
        self._domain_key = domain_key
        self._on_select = on_select
        self._selected = False
        self._hover_count = 0

        self.configure(height=36, cursor="hand2")
        self.pack_propagate(False)

        self._icon_label = tk.Label(
            self,
            text=icon,
            font=("TkDefaultFont", 14),
            anchor="w",
        )
        self._icon_label.pack(side="left", padx=(8, 4))

        self._text_label = tk.Label(
            self,
            text=label,
            font=("TkDefaultFont", 10),
            anchor="w",
        )
        self._text_label.pack(side="left")

        self._badge = tk.Label(
            self,
            text=str(count),
            font=("TkDefaultFont", 9, "bold"),
            width=4,
            anchor="e",
        )
        self._badge.pack(side="right", padx=(0, 8))

        self._bind_events()
        self._apply_style()

    @property
    def _hovered(self) -> bool:
        return self._hover_count > 0

    def _bind_events(self) -> None:
        for widget in (self, self._icon_label, self._text_label, self._badge):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    def _on_click(self, _event: tk.Event) -> None:
        self._on_select(self._domain_key)

    def _on_enter(self, _event: tk.Event) -> None:
        was_hovered = self._hovered
        self._hover_count += 1
        if not self._selected and not was_hovered:
            self._apply_style()

    def _on_leave(self, _event: tk.Event) -> None:
        was_hovered = self._hovered
        self._hover_count = max(0, self._hover_count - 1)
        if not self._selected and was_hovered and not self._hovered:
            self._apply_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            bg = "#2C3E50"
            fg = "#FFFFFF"
        elif self._hovered:
            bg = "#E8E9F0"
            fg = "#333333"
        else:
            bg = "#F5F6FA"
            fg = "#333333"

        self.configure(bg=bg)
        self._icon_label.configure(bg=bg, fg=fg)
        self._text_label.configure(bg=bg, fg=fg)
        self._badge.configure(bg=bg, fg=fg)


class SidebarFrame(tk.Frame):
    DOMAIN_ICONS: dict[str, str] = {
        "light": "\U0001F4A1",
        "switch": "\u26A1",
        "sensor": "\U0001F321",
        "binary_sensor": "\U0001F4E1",
        "climate": "\u2744\uFE0F",
        "cover": "\U0001FA9F",
        "media_player": "\U0001F4FA",
        "fan": "\U0001F300",
        "lock": "\U0001F512",
        "scene": "\U0001F3AC",
        "automation": "\U0001F916",
        "script": "\U0001F4DC",
        "vacuum": "\U0001F9F9",
        "camera": "\U0001F4F7",
        "device_tracker": "\U0001F4CD",
    }

    WIDTH = 220

    def __init__(
        self,
        parent: tk.Widget,
        device_manager: Any,
        event_bus: EventBus | None,
        on_category_selected: Callable[[str | None], None],
        **kw,
    ):
        super().__init__(parent, width=self.WIDTH, **kw)
        self._device_manager = device_manager
        self._event_bus = event_bus
        self._on_category_selected = on_category_selected
        self._category_items: list[_CategoryItem] = []
        self._selected_domain: str | None = None
        self._bridge: AsyncTkBridge | None = None

        self.pack_propagate(False)
        self.grid_propagate(False)
        self.configure(bg="#F5F6FA")

        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_separator(1)
        self._build_scrollable_list()
        self._build_separator(3)
        self._build_status_bar()
        self._bind_events()

    def set_bridge(self, bridge: AsyncTkBridge) -> None:
        self._bridge = bridge

    def _build_header(self) -> None:
        header = tk.Frame(self, bg="#F5F6FA", height=44)
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)
        header.grid_propagate(False)

        title = tk.Label(
            header,
            text="\U0001F3E0 Smart Room",
            font=("TkDefaultFont", 13, "bold"),
            bg="#F5F6FA",
            fg="#2C3E50",
            anchor="w",
        )
        title.pack(fill="both", padx=12, pady=10)

    def _build_separator(self, row: int) -> None:
        sep = tk.Frame(self, bg="#D0D0D0", height=1)
        sep.grid(row=row, column=0, sticky="ew", padx=8)
        sep.grid_propagate(False)

    def _build_scrollable_list(self) -> None:
        container = tk.Frame(self, bg="#F5F6FA")
        container.grid(row=2, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            container,
            highlightthickness=0,
            bg="#F5F6FA",
            width=self.WIDTH,
        )
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._vscroll = ttk.Scrollbar(
            container, orient="vertical", command=self._canvas.yview
        )
        self._vscroll.grid(row=0, column=1, sticky="ns")

        self._canvas.configure(yscrollcommand=self._vscroll.set)

        self._list_frame = tk.Frame(self._canvas, bg="#F5F6FA")
        self._canvas_window = self._canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw"
        )

        self._list_frame.bind("<Configure>", self._on_list_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        self._empty_label = tk.Label(
            self._list_frame,
            text="No devices",
            font=("TkDefaultFont", 10),
            fg="#AAAAAA",
            bg="#F5F6FA",
        )
        self._empty_label.pack(expand=True, pady=20)

    def _build_status_bar(self) -> None:
        self._status_frame = tk.Frame(self, bg="#F5F6FA", height=36)
        self._status_frame.grid(row=4, column=0, sticky="ew")
        self._status_frame.pack_propagate(False)

        self._status_label = tk.Label(
            self._status_frame,
            text="\u25CF Online",
            font=("TkDefaultFont", 10, "bold"),
            bg="#F5F6FA",
            fg="#2ECC40",
            anchor="w",
        )
        self._status_label.pack(fill="both", padx=12, pady=6)

    def _bind_events(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        self._event_bus.subscribe(EventType.DEVICE_ADDED, self._on_device_added)

        self.bind("<MouseWheel>", self._on_mousewheel)

    def _on_state_changed(self, **event_data: Any) -> None:
        if self._bridge is not None:
            self._bridge.schedule_ui(self._refresh_from_event, event_data)
        else:
            self._refresh_from_event(event_data)

    def _on_device_added(self, **event_data: Any) -> None:
        if self._bridge is not None:
            self._bridge.schedule_ui(self._refresh_from_event, event_data)
        else:
            self._refresh_from_event(event_data)

    def _refresh_from_event(self, event_data: dict) -> None:
        device_manager = self._device_manager
        devices_attr = getattr(device_manager, "devices", None)
        if devices_attr is not None:
            if callable(devices_attr):
                devices_dict = devices_attr()
            else:
                devices_dict = devices_attr
            if isinstance(devices_dict, dict):
                self.refresh(list(devices_dict.values()))

    def refresh(self, devices: list) -> None:
        domain_counts: dict[str, int] = {}
        domain_icons: dict[str, str] = {}

        for device in devices:
            domain_str = resolve_domain(device)
            if domain_str == "unknown":
                continue
            domain_counts[domain_str] = domain_counts.get(domain_str, 0) + 1
            if domain_str not in domain_icons:
                domain_icons[domain_str] = self.DOMAIN_ICONS.get(domain_str, "\U0001F4E6")

        sorted_domains = self._sort_domains(list(domain_counts.keys()))

        self._rebuild_list(sorted_domains, domain_counts, domain_icons)

    def _sort_domains(self, domains: list[str]) -> list[str]:
        priority: list[str] = []
        rest: list[str] = []

        for d in domains:
            if d in SORT_ORDER:
                priority.append(d)
            else:
                rest.append(d)

        priority.sort(key=lambda d: SORT_ORDER[d])
        rest.sort()
        return priority + rest

    def _rebuild_list(
        self,
        domains: list[str],
        domain_counts: dict[str, int],
        domain_icons: dict[str, str],
    ) -> None:
        for child in self._list_frame.winfo_children():
            child.destroy()

        self._category_items.clear()

        self._empty_label = tk.Label(
            self._list_frame,
            text="No devices",
            font=("TkDefaultFont", 10),
            fg="#AAAAAA",
            bg="#F5F6FA",
        )

        total_count = sum(domain_counts.values())

        all_item = _CategoryItem(
            self._list_frame,
            domain_key=None,
            icon="\U0001F4CB",
            label="All Devices",
            count=total_count,
            on_select=self._on_category_click,
        )
        all_item.pack(fill="x")
        self._category_items.append(all_item)

        for domain_str in domains:
            count = domain_counts[domain_str]
            if count == 0:
                continue

            icon = domain_icons.get(domain_str, "\U0001F4E6")
            label = DOMAIN_LABELS.get(domain_str, domain_str.replace("_", " ").title())

            item = _CategoryItem(
                self._list_frame,
                domain_key=domain_str,
                icon=icon,
                label=label,
                count=count,
                on_select=self._on_category_click,
            )
            item.pack(fill="x")
            self._category_items.append(item)

        if total_count == 0:
            self._empty_label.pack(expand=True, pady=20)

        if self._selected_domain is None and total_count > 0:
            all_item.set_selected(True)

        self._update_scroll_region()

    def _on_category_click(self, domain: str | None) -> None:
        self._select_item(domain)
        self._on_category_selected(domain)

    def _select_item(self, domain: str | None) -> None:
        self._selected_domain = domain
        for item in self._category_items:
            item.set_selected(item._domain_key == domain)

    def set_selected(self, domain: str | None) -> None:
        self._select_item(domain)

    def set_online(self, online: bool) -> None:
        if online:
            self._status_label.configure(text="\u25CF Online", fg="#2ECC40")
        else:
            self._status_label.configure(text="\u25CF Offline", fg="#E74C3C")

    def _on_list_frame_configure(self, _event: tk.Event) -> None:
        self._update_scroll_region()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._canvas_window, width=event.width)

    def _update_scroll_region(self) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
