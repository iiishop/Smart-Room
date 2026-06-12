"""Main window — source palette (left) + added source cards (right)."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)


# ── Source palette (left sidebar) ───────────────────────────────

class PaletteCard(QFrame):
    """One source type in the palette. Click to add."""

    add_requested = Signal(str)  # source_type

    def __init__(self, source_type: str, label: str, description: str, parent=None):
        super().__init__(parent)
        self.setObjectName("paletteCard")
        self._source_type = source_type
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)

        name = QLabel(label)
        name.setObjectName("paletteName")
        layout.addWidget(name)

        desc = QLabel(description)
        desc.setObjectName("paletteDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

    def mousePressEvent(self, event):
        self.add_requested.emit(self._source_type)
        super().mousePressEvent(event)


class SourcePalette(QScrollArea):
    """Scrollable list of available source types."""

    add_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sourcePalette")
        self.setWidgetResizable(True)
        self.setFixedWidth(260)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(6)

        title = QLabel("Sources")
        title.setObjectName("paletteTitle")
        self._layout.addWidget(title)

        items = [
            ("mqtt", "MQTT Broker", "Connect to an MQTT broker and subscribe to topics."),
            ("mdns", "mDNS Scanner", "Discover mDNS/Bonjour services on the local network."),
            ("ssdp", "SSDP Scanner", "Discover UPnP devices via SSDP multicast."),
        ]
        for stype, label, desc in items:
            card = PaletteCard(stype, label, desc)
            card.add_requested.connect(self.add_requested.emit)
            self._layout.addWidget(card)

        self._layout.addStretch()
        self.setWidget(container)


# ── Source panel (right side, one per added source) ──────────────

class SourcePanel(QGroupBox):
    """One added source — shows status, stats, and event table."""

    edit_requested = Signal(str, str, dict)  # source_id, source_type, settings

    def __init__(self, source_id: str, source_type: str, settings: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("sourcePanel")
        self.source_id = source_id
        self.source_type = source_type
        self._settings = settings
        self._msg_count = 0
        self._start_time = time.time()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Title bar
        title_bar = QHBoxLayout()
        self._dot = QLabel("○")
        self._dot.setObjectName("panelDot")
        title_bar.addWidget(self._dot)

        name = QLabel(source_id)
        name.setObjectName("panelName")
        title_bar.addWidget(name)

        stype_label = QLabel(source_type.upper())
        stype_label.setObjectName("panelType")
        title_bar.addWidget(stype_label)

        title_bar.addStretch()
        self._stats = QLabel("msgs: 0  rate: 0/s")
        self._stats.setObjectName("panelStats")
        title_bar.addWidget(self._stats)
        layout.addLayout(title_bar)

        # Key settings preview
        preview = ", ".join(
            f"{k}={v}" for k, v in sorted(settings.items())
            if v not in (None, "", [])
        )[:200]
        if preview:
            preview_label = QLabel(preview)
            preview_label.setObjectName("panelPreview")
            preview_label.setWordWrap(True)
            layout.addWidget(preview_label)

        # Event table
        self._table = QTableWidget(0, 2)
        self._table.setObjectName("panelTable")
        self._table.setHorizontalHeaderLabels(["Time", "Event"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setMaximumHeight(160)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

    def mouseDoubleClickEvent(self, event):
        self.edit_requested.emit(self.source_id, self.source_type, self._settings)
        super().mouseDoubleClickEvent(event)

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
            item.setForeground(QColor("#ff5555"))
        elif event_type == "discovery":
            item.setForeground(QColor("#50fa7b"))
        self._table.setItem(row, 1, item)
        self._table.scrollToBottom()

        self._msg_count += 1
        elapsed = max(time.time() - self._start_time, 1)
        self._stats.setText(f"msgs: {self._msg_count}  rate: {self._msg_count / elapsed:.1f}/s")

    def update_status(self, connected: bool) -> None:
        self._dot.setText("●" if connected else "○")
        self._dot.setStyleSheet(
            "font-size: 14pt; color: #50fa7b; background: transparent;"
            if connected else
            "font-size: 14pt; color: #6272a4; background: transparent;"
        )

    def update_settings(self, settings: dict) -> None:
        self._settings = settings
        preview = ", ".join(
            f"{k}={v}" for k, v in sorted(settings.items())
            if v not in (None, "", [])
        )[:200]
        for i in range(self.layout().count()):
            w = self.layout().itemAt(i).widget()
            if isinstance(w, QLabel) and w.objectName() == "panelPreview":
                w.setText(preview)
                break

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


# ── Main window ──────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Discover Client main window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Discover Client")
        self.resize(1060, 700)

        self._panels: dict[str, SourcePanel] = {}

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left: source palette
        self.palette = SourcePalette()
        self.palette.add_requested.connect(self._on_add_source)
        root.addWidget(self.palette)

        # Right: added sources
        right = QVBoxLayout()
        right.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.setObjectName("toolbar")
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("btnStart")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_stop)
        right.addLayout(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._card_layout.setSpacing(8)
        self._card_layout.addStretch()
        self.scroll.setWidget(self._card_container)
        right.addWidget(self.scroll)

        root.addLayout(right, stretch=1)

        # Worker
        from discover_client.gui.worker import Worker

        self.worker = Worker()
        self.worker.event_received.connect(self._on_event)
        self.worker.status_changed.connect(self._on_status)

        # Right side starts empty — user adds sources via palette

    # ── Palette interaction ──────────────────────────────────

    def _on_add_source(self, source_type: str) -> None:
        from discover_client.gui.source_dialog import SourceDialog
        dlg = SourceDialog(source_type, self)
        dlg.saved.connect(self._on_source_saved)
        dlg.exec()

    def _on_source_saved(self, source_id: str, source_type: str, settings: dict) -> None:
        if source_id in self._panels:
            self._panels[source_id].update_settings(settings)
            self._save_config()
            return
        self._add_panel(source_id, source_type, settings)
        self._save_config()

    def _add_panel(self, source_id: str, source_type: str, settings: dict) -> None:
        panel = SourcePanel(source_id, source_type, settings)
        panel.edit_requested.connect(self._on_edit_source)
        self._card_layout.addWidget(panel)
        self._panels[source_id] = panel

    def _on_edit_source(self, source_id: str, source_type: str, settings: dict) -> None:
        from discover_client.gui.source_dialog import SourceDialog
        dlg = SourceDialog(source_type, self, edit_source_id=source_id, edit_settings=settings)
        dlg.saved.connect(self._on_source_saved)
        dlg.exec()

    # ── Config persistence ───────────────────────────────────

    def _save_config(self) -> None:
        lines = ["# discover_client/config.toml\n"]
        for sid, panel in self._panels.items():
            lines.append(f"\n[[sources]]")
            lines.append(f'source_id = "{sid}"')
            lines.append(f'source_type = "{panel.source_type}"')
            lines.append(f"enabled = true\n")
            lines.append("[sources.settings]")
            for key, val in sorted(panel._settings.items()):
                if isinstance(val, list):
                    items = ", ".join(f'"{v}"' for v in val)
                    lines.append(f"{key} = [{items}]")
                elif isinstance(val, (int, float)):
                    lines.append(f"{key} = {val}")
                elif val is None:
                    lines.append(f'{key} = ""')
                else:
                    lines.append(f'{key} = "{val}"')

        config_path = Path(__file__).resolve().parent.parent / "config.toml"
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Start / Stop ─────────────────────────────────────────

    def _on_start(self) -> None:
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.worker.start()

    def _on_stop(self) -> None:
        self.worker.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        for panel in self._panels.values():
            panel.update_status(False)

    # ── Events from worker ───────────────────────────────────

    def _on_event(self, source_id: str, event_type: str, payload: dict) -> None:
        if source_id in self._panels:
            self._panels[source_id].add_event(event_type, payload)

    def _on_status(self, source_id: str, connected: bool) -> None:
        if source_id in self._panels:
            self._panels[source_id].update_status(connected)


# ── Entry point ──────────────────────────────────────────────────

def main() -> int:
    app = QApplication([])
    qss_path = Path(__file__).resolve().parent / "style.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
