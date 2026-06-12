"""Main window with Config and Monitor tabs."""

import time
from datetime import datetime

from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import qt_material


class ConfigTab(QWidget):
    """Config tab: source list (left) + auto-generated form (right)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from discover_client.gui.form_builder import FormBuilder
        from discover_client.gui.source_cards import SourceList

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.source_list = SourceList()
        layout.addWidget(self.source_list)

        self.form = FormBuilder()
        layout.addWidget(self.form, stretch=1)

        self.source_list.source_selected.connect(self.form.load_source)
        self.form.saved.connect(self._on_saved)

    def _on_saved(self, source_id: str, settings: dict) -> None:
        print(f"[ConfigTab] Saved {source_id}: {settings}")


class MonitorTab(QWidget):
    """Monitor tab: per-source event tables with statistics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._panel_container = QWidget()
        self._panel_layout = QVBoxLayout(self._panel_container)
        self._panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._panel_container)
        layout.addWidget(scroll)

        self._panels: dict[str, SourceMonitorPanel] = {}

    def set_sources(self, sources: list[dict]) -> None:
        """Build panels for each source."""
        for src in sources:
            source_id = src["source_id"]
            if source_id not in self._panels:
                panel = SourceMonitorPanel(
                    source_id,
                    src["source_type"],
                    src.get("enabled", True),
                )
                self._panel_layout.addWidget(panel)
                self._panels[source_id] = panel
            else:
                self._panels[source_id].update_status(src.get("enabled", True))

    def add_event(self, source_id: str, event_type: str, payload: dict) -> None:
        """Push an event to the appropriate source panel."""
        if source_id in self._panels:
            self._panels[source_id].add_event(event_type, payload)

    def update_status(self, source_id: str, enabled: bool) -> None:
        if source_id in self._panels:
            self._panels[source_id].update_status(enabled)


class SourceMonitorPanel(QGroupBox):
    """One source's event table with header statistics."""

    def __init__(
        self,
        source_id: str,
        source_type: str,
        enabled: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("monitorPanel")
        self.source_id = source_id
        self._msg_count = 0
        self._start_time = time.time()

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._dot = QLabel("●" if enabled else "○")
        self._dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )
        header.addWidget(self._dot)

        title = QLabel(source_id)
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(title)

        type_label = QLabel(f"({source_type})")
        type_label.setStyleSheet("color: #90caf9; font-size: 11px;")
        header.addWidget(type_label)

        header.addStretch()
        self._stats_label = QLabel("msgs: 0  rate: 0/s")
        self._stats_label.setObjectName("statsLabel")
        header.addWidget(self._stats_label)
        layout.addLayout(header)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Time", "Event"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setMaximumHeight(200)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

    def add_event(self, event_type: str, payload: dict) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        summary = self._summarize(event_type, payload)

        row = self._table.rowCount()
        if row >= 200:
            self._table.removeRow(0)
            row = 199
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(ts))
        item = QTableWidgetItem(summary)
        if event_type == "error":
            item.setForeground(QColor("#ef5350"))
        elif event_type == "discovery":
            item.setForeground(QColor("#66bb6a"))
        self._table.setItem(row, 1, item)
        self._table.scrollToBottom()

        self._msg_count += 1
        elapsed = max(time.time() - self._start_time, 1)
        self._stats_label.setText(
            f"msgs: {self._msg_count}  rate: {self._msg_count / elapsed:.1f}/s"
        )

    def _summarize(self, event_type: str, payload: dict) -> str:
        if event_type == "data":
            topic = payload.get("topic", "")
            value = payload.get("value", "")
            return f"{topic} -> {value}"
        if event_type == "discovery":
            name = payload.get("name") or payload.get("service_type", "")
            host = payload.get("host", "")
            return f"+ {name} @ {host}"
        if event_type == "status":
            return payload.get("msg", str(payload))
        if event_type == "error":
            return f"ERROR: {payload.get('msg', str(payload))}"
        return str(payload)

    def update_status(self, enabled: bool) -> None:
        self._dot.setText("●" if enabled else "○")
        self._dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )


class MainWindow(QMainWindow):
    """Discover Client main window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Discover Client")
        self.resize(1000, 680)

        from discover_client.gui.worker import Worker
        from discover_client.config import load_config

        self.worker = Worker()
        self.worker.event_received.connect(self._on_event)
        self.worker.status_changed.connect(self._on_status)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.setObjectName("toolbar")
        self.btn_start = QPushButton("▶ Start")
        self.btn_start.setObjectName("btnStart")
        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_stop)
        root.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.config_tab = ConfigTab()
        self.monitor_tab = MonitorTab()
        self.tabs.addTab(self.config_tab, "Config")
        self.tabs.addTab(self.monitor_tab, "Monitor")
        root.addWidget(self.tabs)

        # Load initial source list
        try:
            configs = load_config()
            sources = [
                {"source_id": c.source_id, "source_type": c.source_type,
                 "enabled": c.enabled, "settings": c.settings}
                for c in configs
            ]
            self.config_tab.source_list.set_sources(sources)
            self.monitor_tab.set_sources(sources)
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _on_start(self) -> None:
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.worker.start()

    def _on_stop(self) -> None:
        self.worker.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_event(self, source_id: str, event_type: str, payload: dict) -> None:
        self.monitor_tab.add_event(source_id, event_type, payload)

    def _on_status(self, source_id: str, enabled: bool) -> None:
        self.monitor_tab.update_status(source_id, enabled)
        self.config_tab.source_list.update_status(source_id, enabled)


def main() -> int:
    from pathlib import Path
    app = QApplication([])

    # Load PyDracula-style dark QSS
    qss_path = Path(__file__).resolve().parent / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
