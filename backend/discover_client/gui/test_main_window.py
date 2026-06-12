import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QTabWidget

from discover_client.gui.main_window import MainWindow


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeWorker(QObject):
    event_received = Signal(str, str, float, str, dict)
    status_changed = Signal(str, bool)


fake_worker_module = types.ModuleType("discover_client.gui.worker")
fake_worker_module.Worker = _FakeWorker
sys.modules["discover_client.gui.worker"] = fake_worker_module


def test_main_window_adds_evidence_tab_and_annotator_rows() -> None:
    _get_app()
    window = MainWindow()
    window._add_panel("mqtt-lab", "mqtt", {"host": "broker.local"})

    tabs = window.findChild(QTabWidget, "mainTabs")

    assert tabs is not None
    assert tabs.count() == 2
    assert tabs.tabText(0) == "Discover"
    assert tabs.tabText(1) == "Evidence"

    window._on_event(
        "mqtt-lab",
        "mqtt",
        1710000000.0,
        "data",
        {"topic": "govee/lamp/state", "value": {"temp": 22}},
    )

    assert window._evidence_table.rowCount() == 1
    assert window._evidence_table.item(0, 1).text() == "mqtt-lab"
    assert window._evidence_table.item(0, 2).text() == "MQTT"
    assert "topic=govee/lamp/state" in window._evidence_table.item(0, 3).text()
