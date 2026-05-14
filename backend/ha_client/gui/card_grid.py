"""CardGridView - scrollable card grid with search/filter and EventBus integration."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from typing import Any

from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.gui.device_card import DeviceCard
from ha_client.gui.widget_factory import WidgetFactory

logger = logging.getLogger(__name__)


class CardGridView(tk.Frame):
    """Card-based device grid with search, domain filter, and responsive layout."""

    CARD_WIDTH = 208
    CARD_HEIGHT = 180
    CARD_SPACING = 12
    COLUMN_BREAKPOINTS = [
        (800, 2),
        (1100, 3),
        (1400, 4),
        (float("inf"), 5),
    ]

    def __init__(
        self,
        parent: tk.Widget,
        device_manager: Any,
        controller: Any,
        bridge: AsyncTkBridge,
        **kw,
    ):
        super().__init__(parent, **kw)
        self._device_manager = device_manager
        self._controller = controller
        self._bridge = bridge

        self._all_devices: list[Any] = []
        self._domain_filter: str | None = None
        self._search_query: str = ""
        self._cards: dict[str, DeviceCard] = {}
        self._services: dict | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_search_bar()
        self._build_scrollable_grid()
        self._bind_events()
        self._discover_services()
        self._show_loading()

    def set_domain_filter(self, domain: str | None) -> None:
        self._domain_filter = domain.lower() if domain else None
        self._apply_filters_and_layout()

    def set_search_query(self, query: str) -> None:
        self._search_query = query.strip().lower()
        self._apply_filters_and_layout()

    def refresh(self, devices: list) -> None:
        self._all_devices = list(devices)
        self._apply_filters_and_layout()

    def update_device(self, entity_id: str, device: Any) -> None:
        card = self._cards.get(entity_id)
        if card is not None:
            state = str(getattr(device, "state", "unknown"))
            attributes = getattr(device, "attributes", {}) or {}
            card.update_state(state, attributes)
            return

        for i, d in enumerate(self._all_devices):
            if str(getattr(d, "entity_id", "")) == entity_id:
                self._all_devices[i] = device
                self._apply_filters_and_layout()
                return

        self._all_devices.append(device)
        self._apply_filters_and_layout()

    def _build_search_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        bar.grid_columnconfigure(1, weight=1)

        ttk.Label(bar, text="\U0001F50D", font=("TkDefaultFont", 12)).grid(
            row=0, column=0, sticky="w", padx=(0, 4)
        )

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_args: self._on_search_changed())

        self._search_entry = ttk.Entry(bar, textvariable=self._search_var)
        self._search_entry.grid(row=0, column=1, sticky="ew")
        self._search_entry.insert(0, "Search devices...")
        self._search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self._search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self._search_entry.configure(foreground="#AAAAAA")

    def _build_scrollable_grid(self) -> None:
        container = tk.Frame(self)
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(container, highlightthickness=0, bg="#F0F0F0")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._vscroll = ttk.Scrollbar(container, orient="vertical", command=self._canvas.yview)
        self._vscroll.grid(row=0, column=1, sticky="ns")

        self._canvas.configure(yscrollcommand=self._vscroll.set)

        self._grid_frame = tk.Frame(self._canvas, bg="#F0F0F0")
        self._canvas_window = self._canvas.create_window((0, 0), window=self._grid_frame, anchor="nw")

        self._grid_frame.bind("<Configure>", self._on_grid_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.bind("<Destroy>", lambda _e: self._canvas.unbind_all("<MouseWheel>"))

        self._placeholder = ttk.Label(
            self._grid_frame,
            text="No devices found",
            font=("TkDefaultFont", 14),
            foreground="#AAAAAA",
            background="#F0F0F0",
        )
        self._placeholder.pack(expand=True, pady=40)

    def _bind_events(self) -> None:
        event_bus = getattr(self._device_manager, "event_bus", None)
        if event_bus is None:
            return

        event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        event_bus.subscribe(EventType.DEVICE_ADDED, self._on_device_added)

    def _discover_services(self) -> None:
        conn_mgr = getattr(self._device_manager, "connection_mgr", None)
        rest = getattr(conn_mgr, "rest", None) if conn_mgr is not None else None
        if rest is None:
            logger.warning("No REST client available for service discovery")
            return

        async def fetch_services():
            return await rest.get_services()

        try:
            future = self._bridge.run_async_background(fetch_services())
            future.add_done_callback(self._on_services_loaded)
        except Exception:
            logger.exception("Failed to start service discovery")

    def _on_services_loaded(self, future) -> None:
        try:
            result = future.result()
            self._services = result
        except Exception:
            logger.exception("Service discovery failed, using default controls")
            self._services = None
        self._bridge.schedule_ui(self._apply_filters_and_layout)

    def _on_search_changed(self) -> None:
        query = self._search_var.get().strip()
        if query == "Search devices...":
            query = ""
        self.set_search_query(query)

    def _on_search_focus_in(self, _event) -> None:
        if self._search_var.get() == "Search devices...":
            self._search_var.set("")
            self._search_entry.configure(foreground="#333333")

    def _on_search_focus_out(self, _event) -> None:
        if not self._search_var.get().strip():
            self._search_var.set("Search devices...")
            self._search_entry.configure(foreground="#AAAAAA")

    def _on_state_changed(self, **event_data) -> None:
        entity_id = event_data.get("entity_id")
        device = event_data.get("device")
        if entity_id and device is not None:
            self._bridge.schedule_ui(self.update_device, entity_id, device)

    def _on_device_added(self, **event_data) -> None:
        entity_id = event_data.get("entity_id")
        device = event_data.get("device")
        if entity_id and device is not None:
            self._bridge.schedule_ui(self.update_device, entity_id, device)

    def _apply_filters_and_layout(self) -> None:
        visible = self._filter_devices()
        self._layout_cards(visible)

    def _filter_devices(self) -> list[Any]:
        result = []
        for device in self._all_devices:
            if not self._device_passes_filters(device):
                continue
            result.append(device)

        result.sort(key=lambda d: str(getattr(d, "entity_id", "")))
        return result

    def _device_passes_filters(self, device: Any) -> bool:
        if self._domain_filter:
            domain = self._resolve_domain(device)
            if domain != self._domain_filter:
                return False

        if self._search_query:
            entity_id = str(getattr(device, "entity_id", "")).lower()
            name = str(getattr(device, "name", "")).lower()
            friendly_name = str(getattr(device, "friendly_name", "")).lower()
            haystack = f"{entity_id} {name} {friendly_name}"
            if self._search_query not in haystack:
                return False

        return True

    def _layout_cards(self, devices: list[Any]) -> None:
        self._placeholder.pack_forget()
        self._cards.clear()
        for child in self._grid_frame.winfo_children():
            if child is not self._placeholder:
                child.destroy()

        if not devices:
            self._placeholder.pack(expand=True, pady=40)
            self._update_scroll_region()
            return

        canvas_width = self._canvas.winfo_width()
        if canvas_width <= 1:
            canvas_width = self.winfo_width()
        if canvas_width <= 1:
            canvas_width = 800

        cols = self._columns_for_width(canvas_width)

        for idx, device in enumerate(devices):
            row = idx // cols
            col = idx % cols

            entity_id = str(getattr(device, "entity_id", ""))
            domain = self._resolve_domain(device)
            state = str(getattr(device, "state", "unknown"))
            attributes = getattr(device, "attributes", {}) or {}
            supported_features = getattr(device, "supported_features", set()) or set()

            card = DeviceCard(
                self._grid_frame,
                entity_id=entity_id,
                domain=domain,
                state=state,
                attributes=attributes,
                supported_features=supported_features,
                available_services=self._services or {},
                on_action=self._on_device_action,
                on_card_click=self._on_card_click,
                bridge=self._bridge,
            )
            x = col * (self.CARD_WIDTH + self.CARD_SPACING) + self.CARD_SPACING
            y = row * (self.CARD_HEIGHT + self.CARD_SPACING) + self.CARD_SPACING
            card.place(x=x, y=y)

            self._cards[entity_id] = card

        total_width = cols * (self.CARD_WIDTH + self.CARD_SPACING) + self.CARD_SPACING
        total_rows = (len(devices) + cols - 1) // cols
        total_height = total_rows * (self.CARD_HEIGHT + self.CARD_SPACING) + self.CARD_SPACING

        self._grid_frame.configure(width=max(total_width, canvas_width - 4), height=total_height)
        self._canvas.configure(scrollregion=(0, 0, total_width, total_height))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._canvas_window, width=event.width)
        self._apply_filters_and_layout()

    def _on_grid_frame_configure(self, _event) -> None:
        self._update_scroll_region()

    def _update_scroll_region(self) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _columns_for_width(self, width: int) -> int:
        for max_w, cols in self.COLUMN_BREAKPOINTS:
            if width < max_w:
                return cols
        return 5

    def _on_card_click(self, entity_id: str) -> None:
        logger.debug("Card clicked: %s", entity_id)

    def _on_device_action(self, entity_id: str, action: str, value: Any = None) -> None:
        if self._controller is None:
            return

        async def _dispatch():
            method = getattr(self._controller, action, None)
            if callable(method):
                if value is not None:
                    return await method(entity_id, value)
                return await method(entity_id)
            return await self._controller.call_service(
                entity_id.split(".")[0], action, entity_id=entity_id
            )

        future = self._bridge.run_async_background(_dispatch())

        def _check_result(fut):
            try:
                fut.result()
            except Exception:
                logger.exception("Device action failed for %s: %s", entity_id, action)
                self._bridge.schedule_ui(
                    lambda: self._show_action_error(entity_id, action)
                )

        future.add_done_callback(_check_result)

    def _show_action_error(self, entity_id: str, action: str) -> None:
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "Action Failed",
                f"Failed to execute '{action}' on {entity_id}.",
                parent=self.winfo_toplevel(),
            )
        except Exception:
            pass

    def _show_loading(self) -> None:
        self._placeholder.configure(text="Loading...")

    @staticmethod
    def _resolve_domain(device: Any) -> str:
        domain = getattr(device, "domain", None)
        if hasattr(domain, "value"):
            return domain.value
        if domain is not None:
            return str(domain).lower()
        entity_id = str(getattr(device, "entity_id", ""))
        if "." in entity_id:
            return entity_id.split(".")[0]
        return "unknown"
