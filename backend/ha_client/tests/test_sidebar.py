"""Unit tests for SidebarFrame category navigation sidebar."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.gui.sidebar import SidebarFrame, resolve_domain


def make_mock_device(entity_id: str, name: str, domain_str: str, state: str = "on"):
    device = MagicMock()
    device.entity_id = entity_id
    device.name = name
    device.state = state
    device.attributes = {}

    domain_mock = MagicMock()
    domain_mock.value = domain_str
    device.domain = domain_mock

    return device


def make_mixed_devices() -> list:
    devices = []
    domains = [
        "light", "light", "light", "light", "light",
        "switch", "switch", "switch", "switch",
        "sensor", "sensor", "sensor",
        "binary_sensor", "binary_sensor",
        "climate", "climate",
        "cover",
        "media_player", "media_player",
        "fan", "fan", "fan",
        "lock",
        "scene",
        "automation",
        "script",
        "vacuum",
    ]
    for i, domain in enumerate(domains):
        devices.append(make_mock_device(f"{domain}.device_{i}", f"Device {i}", domain))
    return devices


@pytest.fixture
def root():
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("Tkinter not available in this environment")
    yield root
    root.destroy()


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def device_manager(event_bus):
    dm = MagicMock()
    dm.devices = {}
    dm.event_bus = event_bus
    return dm


@pytest.fixture
def bridge(root):
    import asyncio
    loop = asyncio.new_event_loop()
    return AsyncTkBridge(root, loop)


@pytest.fixture
def sidebar(root, device_manager, event_bus):
    selected: list = []

    def on_select(domain):
        selected.append(domain)

    sidebar = SidebarFrame(root, device_manager, event_bus, on_select)
    sidebar.pack()
    root.update_idletasks()
    return sidebar, selected


class TestResolveDomain:
    def test_resolve_entity_domain_enum(self):
        device = make_mock_device("light.living", "Living Room", "light")
        assert resolve_domain(device) == "light"

    def test_resolve_string_domain(self):
        device = MagicMock()
        device.domain = "switch"
        assert resolve_domain(device) == "switch"

    def test_resolve_from_entity_id_fallback(self):
        device = MagicMock()
        device.domain = None
        device.entity_id = "cover.curtain"
        assert resolve_domain(device) == "cover"

    def test_resolve_unknown(self):
        device = MagicMock()
        device.domain = None
        device.entity_id = "noprefix"
        assert resolve_domain(device) == "unknown"


class TestSidebarLayout:
    def test_fixed_width_220(self, sidebar):
        widget, _ = sidebar
        assert widget.WIDTH == 220
        assert widget.cget("width") == 220

    def test_structure_elements_exist(self, sidebar, root):
        widget, _ = sidebar
        root.update_idletasks()
        assert hasattr(widget, "_canvas")
        assert hasattr(widget, "_status_label")


class TestSidebarCategoryDisplay:
    def test_30_mixed_devices_displays_all_domains(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        assert len(widget._category_items) >= 1

        all_item = widget._category_items[0]
        assert all_item._domain_key is None
        assert "All" in all_item._text_label.cget("text")

        total = sum(1 for d in devices if resolve_domain(d) != "unknown")
        assert str(total) in all_item._badge.cget("text")

    def test_all_devices_first_default_selected(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        assert widget._selected_domain is None
        assert widget._category_items[0]._domain_key is None
        assert widget._category_items[0]._selected is True

    def test_domain_counts_correct(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        domain_counts = {}
        for d in devices:
            d_str = resolve_domain(d)
            if d_str != "unknown":
                domain_counts[d_str] = domain_counts.get(d_str, 0) + 1

        for item in widget._category_items:
            if item._domain_key is None:
                continue
            expected_count = domain_counts.get(item._domain_key, 0)
            assert str(expected_count) in item._badge.cget("text")

    def test_empty_devices_shows_no_category_items(self, sidebar, root):
        widget, _ = sidebar
        widget.refresh([])
        root.update_idletasks()

        assert len(widget._category_items) == 1
        assert widget._category_items[0]._domain_key is None
        assert "0" in widget._category_items[0]._badge.cget("text")


class TestSidebarInteraction:
    def test_click_all_devices_calls_handler_with_none(self, sidebar, root):
        widget, selected = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        all_item = widget._category_items[0]
        all_item.event_generate("<Button-1>")
        root.update_idletasks()

        assert None in selected

    def test_click_lights_calls_handler_with_light(self, sidebar, root):
        widget, selected = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        light_items = [it for it in widget._category_items if it._domain_key == "light"]
        assert light_items, "No light category found"
        light_items[0].event_generate("<Button-1>")
        root.update_idletasks()

        assert "light" in selected

    def test_single_selection_only(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        light_items = [it for it in widget._category_items if it._domain_key == "light"]
        switch_items = [it for it in widget._category_items if it._domain_key == "switch"]
        assert light_items and switch_items

        light_items[0].event_generate("<Button-1>")
        root.update_idletasks()
        assert widget._selected_domain == "light"

        switch_items[0].event_generate("<Button-1>")
        root.update_idletasks()
        assert widget._selected_domain == "switch"

        selected_count = sum(1 for it in widget._category_items if it._selected)
        assert selected_count == 1

    def test_set_selected_updates_highlight(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        widget.set_selected("light")
        root.update_idletasks()
        assert widget._selected_domain == "light"

        light_items = [it for it in widget._category_items if it._domain_key == "light"]
        assert light_items and light_items[0]._selected

    def test_hover_effect_triggers(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        item = widget._category_items[0]
        assert not item._hovered
        item.event_generate("<Enter>")
        root.update_idletasks()
        assert item._hovered
        item.event_generate("<Leave>")
        root.update_idletasks()
        assert not item._hovered

    def test_click_non_existent_domain_no_error(self, sidebar, root):
        widget, selected = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        widget._on_category_click("nonexistent")
        assert "nonexistent" in selected


class TestSidebarRefresh:
    def test_refresh_zero_count_domain_not_shown(self, sidebar, root):
        widget, _ = sidebar
        devices = [
            make_mock_device("light.1", "Light 1", "light"),
            make_mock_device("switch.1", "Switch 1", "switch"),
        ]
        widget.refresh(devices)
        root.update_idletasks()

        domain_keys = {it._domain_key for it in widget._category_items if it._domain_key is not None}
        assert "sensor" not in domain_keys

    def test_refresh_rebuilds_counts(self, sidebar, root):
        widget, _ = sidebar
        devices1 = [
            make_mock_device("light.1", "L1", "light"),
            make_mock_device("light.2", "L2", "light"),
        ]
        widget.refresh(devices1)
        root.update_idletasks()

        light_item = next((it for it in widget._category_items if it._domain_key == "light"), None)
        assert light_item is not None
        assert "2" in light_item._badge.cget("text")

        devices2 = [make_mock_device("light.3", "L3", "light")]
        widget.refresh(devices2)
        root.update_idletasks()

        light_item2 = next((it for it in widget._category_items if it._domain_key == "light"), None)
        assert light_item2 is not None
        assert "1" in light_item2._badge.cget("text")

    def test_refresh_unknown_domain_skipped(self, sidebar, root):
        widget, _ = sidebar
        devices = [
            make_mock_device("light.1", "L1", "light"),
            make_mock_device("unknown.foo", "Unknown", "unknown"),
        ]
        widget.refresh(devices)
        root.update_idletasks()

        domain_keys = {it._domain_key for it in widget._category_items if it._domain_key is not None}
        assert "unknown" not in domain_keys


class TestSidebarConnectionStatus:
    def test_default_online(self, sidebar, root):
        widget, _ = sidebar
        root.update_idletasks()
        status_text = widget._status_label.cget("text")
        assert "Online" in status_text

    def test_set_online_false_shows_offline(self, sidebar, root):
        widget, _ = sidebar
        widget.set_online(False)
        root.update_idletasks()
        status_text = widget._status_label.cget("text")
        assert "Offline" in status_text

    def test_set_online_toggle(self, sidebar, root):
        widget, _ = sidebar
        widget.set_online(False)
        root.update_idletasks()
        assert "Offline" in widget._status_label.cget("text")

        widget.set_online(True)
        root.update_idletasks()
        assert "Online" in widget._status_label.cget("text")


class TestSidebarSorting:
    def test_all_devices_first(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        assert widget._category_items[0]._domain_key is None

    def test_lights_before_switches_before_sensors(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        domain_order = [it._domain_key for it in widget._category_items if it._domain_key is not None]
        light_idx = domain_order.index("light") if "light" in domain_order else 999
        switch_idx = domain_order.index("switch") if "switch" in domain_order else 999
        sensor_idx = domain_order.index("sensor") if "sensor" in domain_order else 999

        if "light" in domain_order and "switch" in domain_order:
            assert light_idx < switch_idx
        if "switch" in domain_order and "sensor" in domain_order:
            assert switch_idx < sensor_idx

    def test_remaining_domains_alphabetical(self, sidebar, root):
        widget, _ = sidebar
        devices = make_mixed_devices()
        widget.refresh(devices)
        root.update_idletasks()

        domain_order = [it._domain_key for it in widget._category_items if it._domain_key is not None]
        priority = {"light", "switch", "sensor"}
        rest = [d for d in domain_order if d not in priority]
        assert rest == sorted(rest)


class TestSidebarEventBus:
    def test_subscribes_to_state_changed(self, sidebar, event_bus):
        _, _ = sidebar
        callbacks = event_bus._subscribers.get(EventType.STATE_CHANGED, [])
        assert len(callbacks) >= 1

    def test_subscribes_to_device_added(self, sidebar, event_bus):
        _, _ = sidebar
        callbacks = event_bus._subscribers.get(EventType.DEVICE_ADDED, [])
        assert len(callbacks) >= 1

    def test_state_changed_triggers_refresh(self, sidebar, root, event_bus, device_manager):
        widget, _ = sidebar
        devices = make_mixed_devices()
        device_manager.devices = {d.entity_id: d for d in devices}

        widget.refresh = MagicMock(wraps=widget.refresh)

        event_bus.emit(EventType.STATE_CHANGED, entity_id="light.1", device=devices[0])
        root.update_idletasks()

        widget.refresh.assert_called()


class TestSidebarBridge:
    def test_set_bridge_stores(self, sidebar, bridge):
        widget, _ = sidebar
        widget.set_bridge(bridge)
        assert widget._bridge is bridge

    def test_event_uses_bridge_when_set(self, sidebar, root, event_bus, device_manager, bridge):
        widget, _ = sidebar
        widget.set_bridge(bridge)
        devices = make_mixed_devices()
        device_manager.devices = devices

        bridge.schedule_ui = MagicMock(wraps=bridge.schedule_ui)

        event_bus.emit(EventType.STATE_CHANGED, entity_id="light.1", device=devices[0])
        root.update_idletasks()

        bridge.schedule_ui.assert_called()
