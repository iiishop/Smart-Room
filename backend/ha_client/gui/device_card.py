"""Device card widget — per-device card integrating WidgetFactory and StateRenderer."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ha_client.gui.async_bridge import AsyncTkBridge

from ha_client.gui.state_renderer import StateRenderer
from ha_client.gui.widget_factory import WidgetFactory

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
    """Single-device card using WidgetFactory and StateRenderer for controls and state."""

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
        bridge: AsyncTkBridge | None = None,
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
        self._state_widgets: list[tk.Widget] = []

        self._friendly_name = self._attributes.get("friendly_name", entity_id)

        self.pack_propagate(False)
        self.configure(width=_CARD_WIDTH)

        self._build_title_bar()
        self._state_frame = self._build_state_area()
        self._controls_frame = self._build_controls_area()
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

        close_btn = tk.Label(
            bar,
            text="[X]",
            bg="#F5F5F5",
            fg="#999999",
            font=("Segoe UI", 9, "bold"),
            padx=8,
            cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda e: self._on_card_click(self._entity_id))

    def _build_state_area(self) -> tk.Frame:
        frame = tk.Frame(self, bg="#FFFFFF", padx=8, pady=4)
        frame.pack(fill=tk.X)

        widgets = StateRenderer.render_state(frame, self._entity_id, self._state, self._attributes)
        for w in widgets:
            w.pack(anchor="w", fill=tk.X)
        self._state_widgets = widgets
        return frame

    def _build_controls_area(self) -> tk.Frame:
        frame = tk.Frame(self, bg="#FFFFFF", padx=8, pady=4)
        frame.pack(fill=tk.X)

        controls = WidgetFactory.build_widgets(
            parent=frame,
            domain=self._domain,
            entity_id=self._entity_id,
            attributes=self._attributes,
            state=self._state,
            supported_features=self._supported_features,
            available_services=self._available_services,
            on_change=self._on_action,
        )

        if not controls:
            tk.Label(
                frame,
                text="No controls",
                bg="#FFFFFF",
                fg="#AAAAAA",
                font=("Segoe UI", 8),
            ).pack(anchor="w")
        else:
            for ctrl in controls:
                ctrl.pack(anchor="w", fill=tk.X, pady=1)

        self._control_widgets = controls
        return frame

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

        for w in self._state_widgets:
            w.destroy()
        self._state_widgets.clear()

        widgets = StateRenderer.render_state(
            self._state_frame, self._entity_id, self._state, self._attributes
        )
        for w in widgets:
            w.pack(anchor="w", fill=tk.X)
        self._state_widgets = widgets

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
