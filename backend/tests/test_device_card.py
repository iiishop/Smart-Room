"""Tests for DeviceCard, AsyncTkBridge, and related functionality."""

from __future__ import annotations

import asyncio
import tkinter as tk
from collections.abc import Callable

import pytest

from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.gui.device_card import DeviceCard


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


class TestAsyncTkBridge:
    def test_init_creates_loop_and_thread(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        try:
            assert bridge.loop is not None
            assert bridge.loop.is_running()
        finally:
            bridge.shutdown()

    def test_run_async_executes_coroutine(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        results = []

        async def _test_coro():
            await asyncio.sleep(0)
            return 42

        def _on_result(val):
            results.append(val)

        try:
            bridge.run_async(_test_coro(), on_result=_on_result)
            import time
            time.sleep(0.2)
            bridge.drain_ui()

            assert 42 in results, f"results={results}"
        finally:
            bridge.shutdown()

    def test_schedule_ui_runs_on_main_thread(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        results = []

        def _callback():
            results.append(True)

        try:
            bridge.schedule_ui(_callback)
            bridge.drain_ui()
            assert True in results
        finally:
            bridge.shutdown()

    def test_shutdown_stops_loop(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        bridge.shutdown()
        assert bridge.loop is None

    def test_run_async_after_shutdown_is_safe(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        bridge.shutdown()

        async def _test_coro():
            pass

        bridge.run_async(_test_coro())


class TestDeviceCard:
    _LIGHT_ATTRS = {
        "friendly_name": "Ceiling Light",
        "brightness": 200,
        "color_temp": 370,
        "min_mireds": 153,
        "max_mireds": 500,
    }
    _CLIMATE_ATTRS = {
        "friendly_name": "Thermostat",
        "current_temperature": 22.5,
        "temperature": 24.0,
        "hvac_action": "heating",
    }
    _SENSOR_ATTRS = {
        "friendly_name": "Temperature Sensor",
        "unit_of_measurement": "C",
        "device_class": "temperature",
    }
    _SWITCH_ATTRS = {"friendly_name": "Plug Switch"}
    _MEDIA_ATTRS = {
        "friendly_name": "TV",
        "media_title": "Breaking Bad",
        "volume_level": 0.6,
    }
    _COVER_ATTRS = {"friendly_name": "Garage Door"}
    _LOCK_ATTRS = {"friendly_name": "Front Door"}

    def _make_card(
        self,
        root: tk.Tk,
        domain: str,
        state: str,
        attrs: dict | None = None,
        bridge=None,
        on_action: Callable | None = None,
        on_card_click: Callable | None = None,
    ) -> DeviceCard:
        entity_id = f"{domain}.test_device"
        attrs = dict(attrs or {})
        attrs.setdefault("friendly_name", "Test Device")

        return DeviceCard(
            parent=root,
            entity_id=entity_id,
            domain=domain,
            state=state,
            attributes=attrs,
            supported_features=set(),
            available_services={},
            on_action=on_action or (lambda e, a, v: None),
            on_card_click=on_card_click or (lambda e: None),
            bridge=bridge,
        )

    def test_light_card_creation(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "light"
        assert card._state == "on"

    def test_switch_card_creation(self, tk_root):
        card = self._make_card(tk_root, "switch", "off", self._SWITCH_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "switch"

    def test_sensor_card_creation(self, tk_root):
        card = self._make_card(tk_root, "sensor", "22.5", self._SENSOR_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "sensor"

    def test_climate_card_creation(self, tk_root):
        card = self._make_card(tk_root, "climate", "heat", self._CLIMATE_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "climate"

    def test_media_player_card_creation(self, tk_root):
        card = self._make_card(tk_root, "media_player", "playing", self._MEDIA_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "media_player"

    def test_cover_card_creation(self, tk_root):
        card = self._make_card(tk_root, "cover", "open", self._COVER_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "cover"

    def test_lock_card_creation(self, tk_root):
        card = self._make_card(tk_root, "lock", "locked", self._LOCK_ATTRS)
        card.pack()
        tk_root.update()
        assert card._domain == "lock"

    def test_set_selected_adds_blue_border(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()

        card.set_selected(True)
        tk_root.update()
        highlight = card.cget("highlightbackground")
        assert highlight.lower() == "#2196f3"

    def test_set_selected_false_removes_highlight(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()

        card.set_selected(True)
        tk_root.update()
        card.set_selected(False)
        tk_root.update()
        assert card._selected is False

    def test_update_state_refreshes_display(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()

        new_attrs = {"friendly_name": "Ceiling Light", "brightness": 128}
        card.update_state("off", new_attrs)
        tk_root.update()

        assert card._state == "off"
        assert card._attributes == new_attrs

    def test_card_click_triggers_callback(self, tk_root):
        clicked = []

        def _on_click(eid):
            clicked.append(eid)

        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS, on_card_click=_on_click)
        card.pack()
        tk_root.update()

        card._handle_click(None)
        tk_root.update()

        assert "light.test_device" in clicked

    def test_action_callback_on_button(self, tk_root):
        actions = []

        def _on_action(eid, action, value):
            actions.append((eid, action, value))

        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS, on_action=_on_action)
        card.pack()
        tk_root.update()

        card._on_action("light.test_device", "turn_on", None)
        assert ("light.test_device", "turn_on", None) in actions

    def test_get_domain_icon_returns_emoji(self):
        assert DeviceCard.get_domain_icon("light") == "\U0001f4a1"
        assert DeviceCard.get_domain_icon("switch") == "\U0001f50c"
        assert DeviceCard.get_domain_icon("sensor") == "\U0001f4ca"
        assert DeviceCard.get_domain_icon("climate").startswith("\U0001f321")
        assert DeviceCard.get_domain_icon("cover") == "\U0001f6aa"
        assert DeviceCard.get_domain_icon("media_player") == "\U0001f4fa"
        assert DeviceCard.get_domain_icon("lock") == "\U0001f512"
        assert DeviceCard.get_domain_icon("fan") == "\U0001f4a8"
        assert DeviceCard.get_domain_icon("scene") == "\U0001f3ac"
        assert DeviceCard.get_domain_icon("automation") == "\U0001f916"
        assert DeviceCard.get_domain_icon("script") == "\U0001f4dc"
        assert DeviceCard.get_domain_icon("binary_sensor") == "\U0001f6a8"
        assert DeviceCard.get_domain_icon("unknown") == "\U00002753"
        assert DeviceCard.get_domain_icon("nonexistent") == "\U00002753"

    def test_with_bridge_passed(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        try:
            card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS, bridge=bridge)
            card.pack()
            tk_root.update()
            assert card._bridge is bridge
        finally:
            bridge.shutdown()

    def test_state_renderer_integration_creates_widgets(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()
        assert len(card._state_widgets) >= 1

    def test_update_state_rebuilds_state_widgets(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()
        old_count = len(card._state_widgets)
        card.update_state("off", self._LIGHT_ATTRS)
        tk_root.update()
        assert len(card._state_widgets) >= 1
        assert card._state == "off"

    def test_controls_integration_creates_widgets(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()
        assert len(card._control_widgets) >= 1

    def test_card_width_is_280(self, tk_root):
        card = self._make_card(tk_root, "light", "on", self._LIGHT_ATTRS)
        card.pack()
        tk_root.update()
        assert card.winfo_reqwidth() == 280


class TestAsyncBridgeIntegration:
    def test_bridge_with_device_card(self, tk_root):
        bridge = AsyncTkBridge(tk_root)
        results = []

        async def _fetch_data():
            await asyncio.sleep(0)
            return {"state": "on", "brightness": 255}

        def _on_fetched(data):
            results.append(data)

        try:
            bridge.run_async(_fetch_data(), on_result=_on_fetched)
            import time
            time.sleep(0.2)
            bridge.drain_ui()

            assert len(results) == 1, f"results={results}"
            assert results[0] == {"state": "on", "brightness": 255}
        finally:
            bridge.shutdown()
