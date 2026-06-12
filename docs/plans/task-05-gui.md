# Task 5: GUI — Config Panel + Monitor Tab — Implementation Plan

> **For Hermes:** Use opencode run with this plan + discover-client-spec.md as context files.

**Goal:** Build a PySide6 + qt-material desktop GUI with two tabs: Config (source list + auto-generated settings form) and Monitor (real-time event tables per source).

**Architecture:** MainWindow hosts QTabWidget with ConfigTab and MonitorTab. Worker thread runs DiscoverClient's asyncio loop, bridges events to Qt via custom signals. FormBuilder auto-generates widgets from SCHEMAS in config.py.

**Tech Stack:** PySide6, qt-material (dark_teal theme), asyncio, threading.

---

### Task 5.1: Create gui package + main window skeleton

**Objective:** Create the gui package with a themed QMainWindow and two empty tabs.

**Files:**
- Create: `backend/discover_client/gui/__init__.py`
- Create: `backend/discover_client/gui/main_window.py`

**Step 1: Create `__init__.py`**

```python
"""Discover Client GUI — PySide6 + qt-material."""
```

**Step 2: Create `main_window.py`**

```python
"""Main window with Config and Monitor tabs."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QApplication,
)

import qt_material


class ConfigTab(QWidget):
    """Placeholder — will be fleshed out in Task 5.2."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Config Tab — coming in Task 5.2"))


class MonitorTab(QWidget):
    """Placeholder — will be fleshed out in Task 5.3."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Monitor Tab — coming in Task 5.3"))


class MainWindow(QMainWindow):
    """Discover Client main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Discover Client")
        self.resize(1000, 680)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_start = QPushButton("▶ Start")
        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setEnabled(False)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_stop)
        root.addLayout(toolbar)

        # Tab widget
        self.tabs = QTabWidget()
        self.config_tab = ConfigTab()
        self.monitor_tab = MonitorTab()
        self.tabs.addTab(self.config_tab, "Config")
        self.tabs.addTab(self.monitor_tab, "Monitor")
        root.addWidget(self.tabs)


def main():
    app = QApplication([])
    qt_material.apply_stylesheet(app, theme="dark_teal.xml")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 3: Verify runs without crash**

Run: `cd backend && timeout 3 uv run python discover_client/gui/main_window.py 2>&1 || true`
Expected: No crash. Window opens briefly then exits (timeout). Output should NOT contain Python traceback.

Note: On Windows, use `timeout 3` (cmd builtin): `cmd //c "timeout 3 & uv run python discover_client/gui/main_window.py"` or just verify import: `uv run python -c "from discover_client.gui.main_window import MainWindow; print('OK')"`.

---

### Task 5.2: Auto-form builder

**Objective:** Create FormBuilder that generates QWidget forms from SCHEMAS in config.py.

**Files:**
- Create: `backend/discover_client/gui/form_builder.py`

**Step 1: Create file**

```python
"""Auto-generate settings forms from source type schemas."""

from PySide6.QtWidgets import (
    QWidget,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QLabel,
    QVBoxLayout,
    QGroupBox,
    QPushButton,
)
from PySide6.QtCore import Signal

from discover_client.config import SCHEMAS

# Maps setting keys to widget factories.
# Each entry: (widget_class, *constructor_args) or just widget_class.
FIELD_MAP = {
    "host": (QLineEdit,),
    "port": (QSpinBox, 1, 65535),
    "username": (QLineEdit,),
    "password": (QLineEdit, "password"),
    "token": (QLineEdit, "password"),
    "base_url": (QLineEdit,),
    "topic_whitelist": (QTextEdit,),
    "topic_blacklist": (QTextEdit,),
    "service_types": (QTextEdit,),
    "search_targets": (QTextEdit,),
    "scan_interval_s": (QSpinBox, 1, 3600),
}


def _create_widget(key: str, current_value) -> QWidget:
    """Create the appropriate widget for a given settings key."""
    if key in FIELD_MAP:
        spec = FIELD_MAP[key]
        if isinstance(spec, tuple):
            cls = spec[0]
            args = spec[1:]
        else:
            cls = spec
            args = ()
        widget = cls(*args)
    elif isinstance(current_value, int):
        widget = QSpinBox()
        widget.setRange(0, 999999)
    elif isinstance(current_value, list):
        widget = QTextEdit()
    else:
        widget = QLineEdit()
    return widget


def _set_widget_value(widget: QWidget, value) -> None:
    """Set the current value of a form widget."""
    if isinstance(widget, QLineEdit):
        if value is None:
            widget.setText("")
        elif isinstance(value, list):
            widget.setText("\n".join(str(v) for v in value))
        else:
            widget.setText(str(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(value) if value is not None else 0)
    elif isinstance(widget, QTextEdit):
        if isinstance(value, list):
            widget.setPlainText("\n".join(str(v) for v in value))
        elif value:
            widget.setPlainText(str(value))
        else:
            widget.setPlainText("")


def _get_widget_value(widget: QWidget):
    """Read the current value from a form widget."""
    if isinstance(widget, QLineEdit):
        return widget.text()
    elif isinstance(widget, QSpinBox):
        return widget.value()
    elif isinstance(widget, QTextEdit):
        text = widget.toPlainText().strip()
        # If the key expects a list, split by lines
        return text.splitlines() if text else []
    return None


class FormBuilder(QGroupBox):
    """Builds a settings form dynamically from a SourceTypeSchema."""

    saved = Signal(str, dict)  # source_id, settings dict

    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self._form = QFormLayout(self)
        self._widgets: dict[str, QWidget] = {}
        self._source_id: str = ""
        self._source_type: str = ""

        self._btn_save = QPushButton("💾 Save")
        self._btn_save.clicked.connect(self._on_save)
        self._form.addRow(self._btn_save)

    def load_source(self, source_id: str, source_type: str, settings: dict) -> None:
        """Rebuild the form for the given source type and settings."""
        # Clear existing widgets
        while self._form.rowCount() > 1:  # keep save button
            self._form.removeRow(0)
        self._widgets.clear()

        self._source_id = source_id
        self._source_type = source_type

        schema = SCHEMAS.get(source_type)
        if schema is None:
            label = QLabel(f"Unknown source type: {source_type}")
            self._form.insertRow(0, label)
            return

        # Build a row for every key in defaults
        all_keys = list(schema.defaults.keys())
        for key in all_keys:
            current = settings.get(key)
            widget = _create_widget(key, current)
            _set_widget_value(widget, current)
            label = key.replace("_", " ").title()
            self._form.insertRow(self._form.rowCount() - 1, label, widget)
            self._widgets[key] = widget

    def _on_save(self) -> None:
        settings = {}
        for key, widget in self._widgets.items():
            settings[key] = _get_widget_value(widget)
        self.saved.emit(self._source_id, settings)
```

**Step 2: Verify import**

Run: `cd backend && uv run python -c "from discover_client.gui.form_builder import FormBuilder, FIELD_MAP; print(f'FormBuilder OK, {len(FIELD_MAP)} field mappings')"`
Expected: `FormBuilder OK, 11 field mappings`

---

### Task 5.3: Config Tab — source list + form

**Objective:** Replace ConfigTab placeholder with real implementation: left panel with source cards, right panel with FormBuilder.

**Files:**
- Modify: `backend/discover_client/gui/main_window.py` (replace ConfigTab)
- Create: `backend/discover_client/gui/source_cards.py`

**Step 1: Create `source_cards.py`**

```python
"""Scrollable source card list for the Config tab."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QScrollArea,
    QPushButton,
    QLabel,
    QHBoxLayout,
    QFrame,
)


class SourceCard(QFrame):
    """One source card in the list. Click to select."""

    clicked = Signal(str, str, dict)  # source_id, source_type, settings

    def __init__(self, source_id: str, source_type: str, enabled: bool, settings: dict, parent=None):
        super().__init__(parent)
        self.source_id = source_id
        self.source_type = source_type
        self._settings = settings

        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        # Status dot
        self.dot = QLabel("●" if enabled else "○")
        self.dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )
        layout.addWidget(self.dot)

        # Type + ID
        text = QVBoxLayout()
        type_label = QLabel(source_type.upper())
        type_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #90caf9;")
        text.addWidget(type_label)
        id_label = QLabel(source_id)
        id_label.setStyleSheet("font-size: 12px;")
        text.addWidget(id_label)
        layout.addLayout(text)

        layout.addStretch()

    def mousePressEvent(self, event):
        self.clicked.emit(self.source_id, self.source_type, self._settings)
        super().mousePressEvent(event)

    def update_enabled(self, enabled: bool) -> None:
        self.dot.setText("●" if enabled else "○")
        self.dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )


class SourceList(QScrollArea):
    """Scrollable list of SourceCards."""

    source_selected = Signal(str, str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFixedWidth(250)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setAlignment(Qt.AlignTop)
        self._layout.setSpacing(4)

        # Logo / title
        title = QLabel("🔍 Sources")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        self._layout.addWidget(title)

        self._cards: dict[str, SourceCard] = {}

        # Add button
        self.btn_add = QPushButton("+ Add Source")
        self._layout.addWidget(self.btn_add)

        self.setWidget(container)

    def set_sources(self, sources: list[dict]) -> None:
        """Rebuild card list from [{source_id, source_type, enabled, settings}, ...]."""
        # Remove old cards (keep title and add button)
        for card in self._cards.values():
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for src in sources:
            card = SourceCard(
                src["source_id"], src["source_type"],
                src.get("enabled", True), src.get("settings", {}),
            )
            card.clicked.connect(self._on_card_clicked)
            # Insert before add button
            self._layout.insertWidget(self._layout.count() - 1, card)
            self._cards[src["source_id"]] = card

    def _on_card_clicked(self, source_id, source_type, settings):
        self.source_selected.emit(source_id, source_type, settings)

    def update_status(self, source_id: str, enabled: bool) -> None:
        if source_id in self._cards:
            self._cards[source_id].update_enabled(enabled)
```

**Step 2: Replace ConfigTab in main_window.py**

Replace the existing `ConfigTab` class in `main_window.py` with:

```python
class ConfigTab(QWidget):
    """Config tab: source list (left) + auto-generated form (right)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        from discover_client.gui.source_cards import SourceList
        from discover_client.gui.form_builder import FormBuilder

        self.source_list = SourceList()
        layout.addWidget(self.source_list)

        self.form = FormBuilder()
        layout.addWidget(self.form, stretch=1)

        self.source_list.source_selected.connect(self.form.load_source)
        self.form.saved.connect(self._on_saved)

    def _on_saved(self, source_id: str, settings: dict) -> None:
        # Will be wired to config persistence in Task 5.5
        print(f"[ConfigTab] Saved {source_id}: {settings}")
```

**Step 3: Verify import and layout**

Run: `cd backend && uv run python -c "from discover_client.gui.main_window import ConfigTab; from discover_client.gui.source_cards import SourceList, SourceCard; from discover_client.gui.form_builder import FormBuilder; print('All config tab modules OK')"`
Expected: `All config tab modules OK`

---

### Task 5.4: Monitor Tab — event tables

**Objective:** Replace MonitorTab placeholder with per-source event tables + statistics.

**Files:**
- Modify: `backend/discover_client/gui/main_window.py` (replace MonitorTab)

**Step 1: Replace MonitorTab in main_window.py**

Replace the existing `MonitorTab` class with:

```python
class MonitorTab(QWidget):
    """Monitor tab: per-source event tables with statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._panel_container = QWidget()
        self._panel_layout = QVBoxLayout(self._panel_container)
        self._panel_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self._panel_container)
        layout.addWidget(scroll)

        self._panels: dict[str, SourceMonitorPanel] = {}

    def set_sources(self, sources: list[dict]) -> None:
        """Build panels for each source."""
        for src in sources:
            sid = src["source_id"]
            if sid not in self._panels:
                panel = SourceMonitorPanel(
                    sid, src["source_type"], src.get("enabled", True)
                )
                self._panel_layout.addWidget(panel)
                self._panels[sid] = panel
            else:
                self._panels[sid].update_status(src.get("enabled", True))

    def add_event(self, source_id: str, event_type: str, payload: dict) -> None:
        """Push an event to the appropriate source panel."""
        if source_id in self._panels:
            self._panels[source_id].add_event(event_type, payload)

    def update_status(self, source_id: str, enabled: bool) -> None:
        if source_id in self._panels:
            self._panels[source_id].update_status(enabled)


class SourceMonitorPanel(QGroupBox):
    """One source's event table with header statistics."""

    def __init__(self, source_id: str, source_type: str, enabled: bool, parent=None):
        super().__init__(parent)
        self.source_id = source_id
        self._msg_count = 0
        self._start_time = __import__("time").time()

        layout = QVBoxLayout(self)

        # Header
        header = QHBoxLayout()
        self._dot = QLabel("●" if enabled else "○")
        self._dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )
        header.addWidget(self._dot)

        title = QLabel(f"{source_id}")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(title)

        type_label = QLabel(f"({source_type})")
        type_label.setStyleSheet("color: #90caf9; font-size: 11px;")
        header.addWidget(type_label)

        header.addStretch()
        self._stats_label = QLabel("msgs: 0  rate: 0/s")
        self._stats_label.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        header.addWidget(self._stats_label)
        layout.addLayout(header)

        # Event table
        from PySide6.QtWidgets import QTableWidget, QHeaderView
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Time", "Event"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.setMaximumHeight(200)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self._table)

    def add_event(self, event_type: str, payload: dict) -> None:
        from datetime import datetime
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
        elapsed = max(__import__("time").time() - self._start_time, 1)
        self._stats_label.setText(
            f"msgs: {self._msg_count}  rate: {self._msg_count / elapsed:.1f}/s"
        )

    def _summarize(self, event_type: str, payload: dict) -> str:
        if event_type == "data":
            topic = payload.get("topic", "")
            value = payload.get("value", "")
            return f"{topic} → {value}"
        elif event_type == "discovery":
            name = payload.get("name", "")
            host = payload.get("host", "")
            return f"+ {name} @ {host}"
        elif event_type == "status":
            return payload.get("msg", str(payload))
        elif event_type == "error":
            return f"ERROR: {payload.get('msg', str(payload))}"
        return str(payload)

    def update_status(self, enabled: bool) -> None:
        self._dot.setText("●" if enabled else "○")
        self._dot.setStyleSheet(
            f"color: {'#4caf50' if enabled else '#757575'}; font-size: 14px;"
        )
```

Need to add imports at top of main_window.py — add to existing imports:
```python
from PySide6.QtWidgets import (
    ...,
    QTableWidget, QHeaderView, QTableWidgetItem, QScrollArea, QGroupBox,
)
from PySide6.QtGui import QColor
```

**Step 2: Verify import**

Run: `cd backend && uv run python -c "from discover_client.gui.main_window import MonitorTab, SourceMonitorPanel; print('Monitor tab modules OK')"`
Expected: `Monitor tab modules OK`

---

### Task 5.5: Worker thread + Start/Stop wiring

**Objective:** Wire Start/Stop buttons to spawn/stop the Discover Client worker thread. Bridge SourceEvents to MonitorTab via Qt signals.

**Files:**
- Create: `backend/discover_client/gui/worker.py`
- Modify: `backend/discover_client/gui/main_window.py` (wire buttons + worker)

**Step 1: Create `worker.py`**

```python
"""Background worker that runs DiscoverClient's asyncio loop."""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from discover_client.client import DiscoverClient
from discover_client.source import SourceEvent
from discover_client.config import load_config


class Worker(QObject):
    """Runs DiscoverClient on a background thread, bridges events to Qt."""

    event_received = Signal(str, str, dict)  # source_id, event_type, payload
    status_changed = Signal(str, bool)        # source_id, enabled

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: DiscoverClient | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_main())
        finally:
            loop.close()

    async def _async_main(self) -> None:
        self._client = DiscoverClient()
        configs = load_config()

        # Subscribe to events → bridge to Qt signals
        def on_event(event: SourceEvent):
            self.event_received.emit(
                event.source_id, event.event_type, event.payload
            )

        self._client.subscribe(on_event)

        try:
            await self._client.start(configs)
            # Keep running until stopped
            while self._running:
                await asyncio.sleep(0.1)
        finally:
            await self._client.stop()
```

**Step 2: Wire buttons in main_window.py**

Replace the `__init__` method's button section and add worker:

```python
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Discover Client")
        self.resize(1000, 680)

        # Worker
        from discover_client.gui.worker import Worker
        from discover_client.config import load_config
        self.worker = Worker()
        self.worker.event_received.connect(self._on_event)
        self.worker.status_changed.connect(self._on_status)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_start = QPushButton("▶ Start")
        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_start)
        toolbar.addWidget(self.btn_stop)
        root.addLayout(toolbar)

        # Tab widget
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

    def _on_start(self):
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.worker.start()

    def _on_stop(self):
        self.worker.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_event(self, source_id: str, event_type: str, payload: dict):
        self.monitor_tab.add_event(source_id, event_type, payload)

    def _on_status(self, source_id: str, enabled: bool):
        self.monitor_tab.update_status(source_id, enabled)
        self.config_tab.source_list.update_status(source_id, enabled)
```

**Step 3: Verify full import chain**

Run: `cd backend && uv run python -c "from discover_client.gui.main_window import MainWindow; from discover_client.gui.worker import Worker; w = MainWindow(); print('MainWindow created OK')"`
Expected: `MainWindow created OK`

Note: This will print a config.toml error if the file has issues — that's fine, it means config loading is wired correctly.

---

### Task 5.6: Cleanup

Delete any temporary test files created during development.

Run: `cd backend && ls discover_client/scan_*.py 2>/dev/null && rm discover_client/scan_*.py 2>/dev/null; echo "cleanup done"`
