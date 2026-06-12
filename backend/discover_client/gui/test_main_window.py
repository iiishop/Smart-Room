import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from discover_client.gui.evidence_page import EvidencePage
from discover_client.gui.main_window import MainWindow
from discover_client.identification.device import Device
from discover_client.identification.evidence import SignalEvidence


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeWorker(QObject):
    event_received = Signal(str, str, float, str, dict)
    evidence_produced = Signal(object)
    dedup_updated = Signal(list)
    status_changed = Signal(str, bool)


fake_worker_module = types.ModuleType("discover_client.gui.worker")
fake_worker_module.Worker = _FakeWorker
sys.modules["discover_client.gui.worker"] = fake_worker_module


def test_evidence_page_creates_expected_tabs() -> None:
    _get_app()
    page = EvidencePage()

    assert page._tabs.count() == 4
    assert page._tabs.tabText(0) == "MQTT"
    assert page._tabs.tabText(1) == "MDNS"
    assert page._tabs.tabText(2) == "SSDP"
    assert page._tabs.tabText(3) == "NMAP"


def test_main_window_routes_worker_evidence_to_evidence_page() -> None:
    _get_app()
    window = MainWindow()
    window._add_panel("mqtt-lab", "mqtt", {"host": "broker.local"})

    assert window._stack.count() == 3
    assert window._stack.currentIndex() == 0

    window._on_event(
        "mqtt-lab",
        "mqtt",
        1710000000.0,
        "data",
        {"topic": "govee/lamp/state", "value": {"temp": 22}},
    )

    window._on_evidence(
        SignalEvidence(
            source_id="mqtt-lab",
            source_type="mqtt",
            mqtt_topic="govee/lamp/state",
            mqtt_payload_keys={"temp"},
            timestamp=1710000000.0,
        )
    )

    assert window._evidence_page._tables["mqtt"].rowCount() == 1
    assert window._evidence_page._tables["mqtt"].item(0, 1).text() == "mqtt-lab"
    assert window._evidence_page._tables["mqtt"].item(0, 2).text() == "govee/lamp/state"
    assert window._evidence_page._tables["mqtt"].item(0, 3).text() == "temp"


def test_main_window_routes_dedup_updates_to_dedup_page() -> None:
    _get_app()
    window = MainWindow()

    device = Device(
        device_id="device-1",
        total_evidence_count=2,
        last_seen=1710000000.0,
        ip_addresses={"192.168.5.2"},
        hostnames={"58044F9ADC05.local."},
        mac_prefixes={"58:04:4F"},
        vendor="TP-Link Systems Inc.",
        service_types={"_matter._tcp.local."},
    )

    assert window._stack.count() == 3

    window._on_dedup_updated([device])

    table = window._dedup_page._table
    assert table.rowCount() == 1
    assert table.item(0, 0).text() == "device-1"
    assert table.item(0, 1).text() == "192.168.5.2"
    assert table.item(0, 2).text() == "58044F9ADC05.local."
    assert table.item(0, 3).text() == "58:04:4F"
    assert table.item(0, 4).text() == "TP-Link Systems Inc."
    assert table.item(0, 5).text() == "_matter._tcp.local."
    assert table.item(0, 6).text() == "2"
