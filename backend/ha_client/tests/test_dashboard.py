"""Integration tests for DashboardApp and main application window."""

from __future__ import annotations

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from ha_client.core.event_bus import EventBus, EventType
from ha_client.gui.async_bridge import AsyncTkBridge
from ha_client.gui.card_grid import CardGridView
from ha_client.gui.dashboard import DashboardApp
from ha_client.gui.sidebar import SidebarFrame
from ha_client.gui.status_bar import StatusBar


def _make_async_none():
    async def _async():
        return None
    return _async()


def _make_async_list():
    async def _async():
        return []
    return _async()


@pytest.fixture(scope="module")
def event_bus():
    return EventBus()


@pytest.fixture(scope="module")
def mock_config():
    cfg = MagicMock()
    cfg.url = "http://localhost:8123"
    cfg.token = "test-token"
    cfg.verify_ssl = False
    cfg.reconnect_interval = 10.0
    cfg.request_timeout = 30.0
    return cfg


@pytest.fixture(scope="module")
def mock_conn_mgr():
    mgr = MagicMock()
    mgr.online = False
    mgr.start = MagicMock(side_effect=_make_async_none)
    mgr.stop = MagicMock(side_effect=_make_async_none)
    return mgr


@pytest.fixture(scope="module")
def mock_device_mgr(mock_conn_mgr, event_bus):
    mgr = MagicMock()
    mgr.connection_mgr = mock_conn_mgr
    mgr.event_bus = event_bus
    mgr.devices = {}
    mgr.load_devices = MagicMock(side_effect=_make_async_list)
    mgr.start_sync = MagicMock(side_effect=_make_async_none)
    return mgr


@pytest.fixture(scope="module")
def mock_controller(mock_device_mgr, event_bus):
    ctrl = MagicMock()
    ctrl._device_manager = mock_device_mgr
    ctrl._event_bus = event_bus
    return ctrl


@pytest.fixture(scope="module")
def app(mock_config, mock_conn_mgr, mock_device_mgr, mock_controller, event_bus):
    app = DashboardApp(
        mock_config, mock_conn_mgr, mock_device_mgr, mock_controller, event_bus
    )
    yield app
    try:
        app._bridge.shutdown()
    except Exception:
        pass
    try:
        app._root.destroy()
    except tk.TclError:
        pass


class TestDashboardAppCreation:
    def test_window_title(self, app):
        assert app._root.title() == "Smart Room Dashboard"

    def test_window_minsize(self, app):
        app._root.update_idletasks()
        min_w, min_h = app._root.minsize()
        assert min_w >= 900
        assert min_h >= 600

    def test_bridge_created(self, app):
        assert isinstance(app._bridge, AsyncTkBridge)
        assert app._bridge.loop is not None

    def test_sidebar_created(self, app):
        assert isinstance(app._sidebar, SidebarFrame)

    def test_card_grid_created(self, app):
        assert isinstance(app._card_grid, CardGridView)

    def test_status_bar_created(self, app):
        assert isinstance(app._status_bar, StatusBar)


class TestDashboardAppCategoryFilter:
    def test_sidebar_category_filters_card_grid(self, app):
        assert app._card_grid._domain_filter is None
        app._on_category_selected("light")
        assert app._card_grid._domain_filter == "light"
        app._on_category_selected(None)
        assert app._card_grid._domain_filter is None


class TestDashboardAppConnectionStatus:
    def test_status_bar_shows_disconnected_initially(self, app):
        assert app._status_bar._current_status == "disconnected"

    def test_connect_shows_connecting_status(self, app, mock_conn_mgr):
        mock_conn_mgr.online = False
        app._on_connect()
        assert app._status_bar._current_status == "connecting"

    def test_disconnect_when_online(self, app, mock_conn_mgr):
        mock_conn_mgr.online = True
        app._on_disconnect()
        assert app._status_bar._current_status == "disconnected"

    def test_event_bus_connection_changed_updates_status_bar(self, app, event_bus):
        event_bus.emit(EventType.CONNECTION_CHANGED, online=True, device_count=5)
        assert app._status_bar._current_status == "connected"
        assert app._status_bar._device_count_var.get() == "Devices: 5"

        event_bus.emit(EventType.CONNECTION_CHANGED, online=False)
        assert app._status_bar._current_status == "disconnected"

    def test_connection_changed_updates_sidebar_online(self, app, event_bus):
        event_bus.emit(EventType.CONNECTION_CHANGED, online=True, device_count=3)
        assert "Online" in app._sidebar._status_label.cget("text")

        event_bus.emit(EventType.CONNECTION_CHANGED, online=False)
        assert "Offline" in app._sidebar._status_label.cget("text")


class TestDashboardAppClose:
    def test_close_when_offline_shuts_down_bridge(self, app, mock_conn_mgr):
        mock_conn_mgr.online = False
        loop = app._bridge.loop
        assert loop is not None
        app._on_close()
        assert app._bridge.loop is None
