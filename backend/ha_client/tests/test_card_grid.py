"""Unit tests for CardGridView using mocked DeviceManager and DeviceController."""

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
from ha_client.gui.card_grid import CardGridView
from ha_client.gui.device_card import DeviceCard
from ha_client.gui.widget_factory import WidgetFactory
from ha_client.gui.state_renderer import StateRenderer


class MockRest:
    async def get_services(self):
        return {
            "light": {"turn_on": {}, "turn_off": {}, "toggle": {}},
            "switch": {"turn_on": {}, "turn_off": {}, "toggle": {}},
            "cover": {"open_cover": {}, "close_cover": {}, "stop_cover": {}},
        }


class MockConnectionMgr:
    def __init__(self):
        self.rest = MockRest()


def make_mock_device_simple(entity_id, name, domain_str, state="on"):
    device = MagicMock()
    device.entity_id = entity_id
    device.name = name
    device.friendly_name = name
    device.state = state
    device.attributes = {}
    device.brightness = MagicMock()
    device.brightness.__int__ = lambda self: 128

    domain_mock = MagicMock()
    domain_mock.value = domain_str
    device.domain = domain_mock

    return device


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
def bridge(root):
    return AsyncTkBridge(root)


@pytest.fixture
def device_manager(event_bus):
    dm = MagicMock()
    dm.devices = {}
    dm.event_bus = event_bus
    dm.connection_mgr = MockConnectionMgr()
    return dm


@pytest.fixture
def controller():
    ctrl = MagicMock()
    return ctrl


@pytest.fixture
def card_grid(root, device_manager, controller, bridge):
    grid = CardGridView(root, device_manager, controller, bridge)
    grid.pack(fill="both", expand=True)
    root.update_idletasks()
    return grid


class TestCardGridViewFilters:
    def test_refresh_with_devices_shows_cards(self, card_grid, root):
        devices = [make_mock_device_simple(f"light.living_room_{i}", f"Living Room Light {i}", "light") for i in range(30)]
        card_grid.refresh(devices)
        root.update_idletasks()

        assert len(card_grid._cards) == 30

    def test_search_filters_by_entity_id(self, card_grid, root):
        devices = [
            make_mock_device_simple("light.kitchen", "Kitchen Light", "light"),
            make_mock_device_simple("switch.bedroom", "Bedroom Switch", "switch"),
            make_mock_device_simple("light.hallway", "Hallway Light", "light"),
        ]
        card_grid.refresh(devices)
        card_grid.set_search_query("kitchen")
        root.update_idletasks()

        assert len(card_grid._cards) == 1
        assert "light.kitchen" in card_grid._cards

    def test_search_filters_by_friendly_name(self, card_grid, root):
        devices = [
            make_mock_device_simple("light.1", "Living Room Light", "light"),
            make_mock_device_simple("switch.1", "Bedroom Switch", "switch"),
        ]
        card_grid.refresh(devices)
        card_grid.set_search_query("bedroom")
        root.update_idletasks()

        assert len(card_grid._cards) == 1
        assert "switch.1" in card_grid._cards

    def test_domain_filter_shows_only_matching(self, card_grid, root):
        devices = [
            make_mock_device_simple("light.kitchen", "Kitchen Light", "light"),
            make_mock_device_simple("switch.bedroom", "Bedroom Switch", "switch"),
            make_mock_device_simple("light.hallway", "Hallway Light", "light"),
        ]
        card_grid.refresh(devices)
        card_grid.set_domain_filter("switch")
        root.update_idletasks()

        assert len(card_grid._cards) == 1
        assert "switch.bedroom" in card_grid._cards

    def test_combined_search_and_domain_filter(self, card_grid, root):
        devices = [
            make_mock_device_simple("light.kitchen", "Kitchen Light", "light"),
            make_mock_device_simple("light.bedroom", "Bedroom Light", "light"),
            make_mock_device_simple("switch.kitchen", "Kitchen Switch", "switch"),
        ]
        card_grid.refresh(devices)
        card_grid.set_domain_filter("light")
        card_grid.set_search_query("bedroom")
        root.update_idletasks()

        assert len(card_grid._cards) == 1
        assert "light.bedroom" in card_grid._cards

    def test_no_devices_shows_placeholder(self, card_grid, root):
        card_grid.refresh([])
        root.update_idletasks()
        assert len(card_grid._cards) == 0

    def test_set_domain_filter_none_resets(self, card_grid, root):
        devices = [
            make_mock_device_simple("light.kitchen", "Kitchen", "light"),
            make_mock_device_simple("switch.bedroom", "Bedroom", "switch"),
        ]
        card_grid.refresh(devices)
        card_grid.set_domain_filter("switch")
        root.update_idletasks()
        assert len(card_grid._cards) == 1

        card_grid.set_domain_filter(None)
        root.update_idletasks()
        assert len(card_grid._cards) == 2


class TestCardGridViewUpdate:
    def test_update_device_updates_existing_card(self, card_grid, root):
        device = make_mock_device_simple("light.kitchen", "Kitchen", "light", "off")
        card_grid.refresh([device])
        root.update_idletasks()

        updated = make_mock_device_simple("light.kitchen", "Kitchen", "light", "on")
        card_grid.update_device("light.kitchen", updated)
        root.update_idletasks()

        card = card_grid._cards.get("light.kitchen")
        assert card is not None

    def test_update_device_adds_new_device(self, card_grid, root):
        card_grid.refresh([])
        root.update_idletasks()

        device = make_mock_device_simple("light.new", "New Light", "light")
        card_grid.update_device("light.new", device)
        root.update_idletasks()

        assert "light.new" in card_grid._cards


class TestCardGridViewColumns:
    def test_columns_for_width(self, card_grid):
        assert card_grid._columns_for_width(400) == 2
        assert card_grid._columns_for_width(799) == 2
        assert card_grid._columns_for_width(800) == 3
        assert card_grid._columns_for_width(1099) == 3
        assert card_grid._columns_for_width(1100) == 4
        assert card_grid._columns_for_width(1399) == 4
        assert card_grid._columns_for_width(1400) == 5
        assert card_grid._columns_for_width(2000) == 5


class TestDeviceCard:
    def test_device_card_creation(self, root, controller):
        card = DeviceCard(
            root,
            entity_id="light.test",
            domain="light",
            state="on",
            attributes={"friendly_name": "Test Light"},
            supported_features={1},
            available_services={},
            on_action=lambda e, a, v: None,
            on_card_click=lambda e: None,
        )
        assert card._entity_id == "light.test"

    def test_device_card_update_state(self, root):
        card = DeviceCard(
            root,
            entity_id="light.test",
            domain="light",
            state="on",
            attributes={"friendly_name": "Test"},
            supported_features={1},
            available_services={},
            on_action=lambda e, a, v: None,
            on_card_click=lambda e: None,
        )
        card.update_state("on", {"friendly_name": "Test", "brightness": 128})
        assert card._state == "on"


class TestWidgetFactory:
    def test_build_light_widgets(self, root):
        parent = tk.Frame(root)
        actions = []
        widgets = WidgetFactory.build_widgets(
            parent=parent,
            domain="light",
            entity_id="light.test",
            attributes={"friendly_name": "Test"},
            state="on",
            supported_features={1},
            available_services={"light": {"turn_off": {}, "turn_on": {}}},
            on_change=lambda e, a, v: actions.append((e, a, v)),
        )
        root.update_idletasks()
        assert len(widgets) > 0

    def test_build_switch_widgets(self, root):
        parent = tk.Frame(root)
        actions = []
        widgets = WidgetFactory.build_widgets(
            parent=parent,
            domain="switch",
            entity_id="switch.test",
            attributes={"friendly_name": "Test"},
            state="off",
            supported_features=set(),
            available_services={"switch": {"turn_on": {}, "turn_off": {}}},
            on_change=lambda e, a, v: actions.append((e, a, v)),
        )
        root.update_idletasks()
        assert len(widgets) > 0


class TestStateRenderer:
    def test_render_light_state(self, root):
        parent = tk.Frame(root)
        widgets = StateRenderer.render_state(
            parent,
            entity_id="light.test",
            state="on",
            attributes={"friendly_name": "Test", "brightness": 128},
        )
        root.update_idletasks()
        assert len(widgets) > 0

    def test_render_switch_off_state(self, root):
        parent = tk.Frame(root)
        widgets = StateRenderer.render_state(
            parent,
            entity_id="switch.test",
            state="off",
            attributes={"friendly_name": "Test"},
        )
        root.update_idletasks()
        assert len(widgets) > 0

    def test_render_sensor_state(self, root):
        parent = tk.Frame(root)
        widgets = StateRenderer.render_state(
            parent,
            entity_id="sensor.test",
            state="22.5",
            attributes={"friendly_name": "Test", "unit_of_measurement": "°C"},
        )
        root.update_idletasks()
        assert len(widgets) > 0


class TestAsyncBridge:
    def test_schedule_ui_runs_callback(self, root, bridge):
        called = []
        bridge.schedule_ui(lambda: called.append(True))
        bridge.drain_ui()
        assert len(called) == 1

    def test_run_async_background(self, bridge):
        import asyncio

        async def coro():
            return 42

        results = []
        bridge.run_async(coro(), on_result=lambda r: results.append(r))
        import time
        time.sleep(0.5)
        bridge.drain_ui()
        assert 42 in results
        bridge.shutdown()
