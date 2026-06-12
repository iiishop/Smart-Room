# Task 07 — Evidence Visualization (Page 2)

## Goal
Add a second page to the GUI that shows annotator output per source type: what evidence each annotator produced from incoming events.

## Architecture

```
Worker._async_main
  ├── event_received(source_id, event_type, payload)    [existing]
  └── evidence_produced(SignalEvidence)                  [NEW signal]

MainWindow
  ├── QStackedWidget(index=0): current layout (toolbar + cards + raw log)
  └── QStackedWidget(index=1): EvidencePage              [NEW]
       └── QVBoxLayout
            ├── toolbar: label "Evidence" + "Back" button
            └── QTabWidget: one tab per source type
                 ├── [MQTT] QTableWidget(Time | Source | Topic | Payload Keys)
                 ├── [mDNS] QTableWidget(Time | Source | Service | Hostname | IP)
                 ├── [SSDP] QTableWidget(Time | Source | USN | Server | IP)
                 └── [NMAP] QTableWidget(Time | Source | IP | Hostname | MAC | Vendor | OS)
```

## Files to modify

1. `discover_client/gui/worker.py` — add `evidence_produced` signal + run annotators
2. `discover_client/gui/main_window.py` — add Page 2 with EvidencePage widget
3. `discover_client/gui/evidence_page.py` — NEW: EvidencePage widget

## Task 7.1 — Worker: emit evidence

In `worker.py`, add signal and run annotators in `on_event`:

```python
# New signal
evidence_produced = Signal(object)  # SignalEvidence

# In _async_main, in on_event callback after the status check:
# Run annotator for this event's source_type
annotator_cls = ANNOTATORS.get(event.source_type)
if annotator_cls is not None:
    annotator = annotator_cls()
    evidence = annotator.annotate(event)
    if evidence is not None:
        self.evidence_produced.emit(evidence)
```

Import ANNOTATORS at top of worker.py:
```python
from discover_client.identification import ANNOTATORS
```

## Task 7.2 — EvidencePage widget (NEW FILE)

`discover_client/gui/evidence_page.py`:

```python
"""Evidence viewer — tab per source type, shows annotator output."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QAbstractItemView,
)
from discover_client.identification.evidence import SignalEvidence


class EvidencePage(QWidget):
    COLUMNS = {
        "mqtt":  ["Time", "Source", "Topic", "Payload Keys"],
        "mdns":  ["Time", "Source", "Service Type", "Hostname", "IP"],
        "ssdp":  ["Time", "Source", "USN", "Server", "IP"],
        "nmap":  ["Time", "Source", "IP", "Hostname", "MAC", "Vendor", "OS"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tables: dict[str, QTableWidget] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Create tab per source type
        for stype, columns in self.COLUMNS.items():
            table = self._make_table(columns)
            self._tables[stype] = table
            self._tabs.addTab(table, stype.upper())

    def _make_table(self, columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setObjectName("evidenceTable")
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setStretchLastSection(True)
        header = table.horizontalHeader()
        for i in range(len(columns) - 1):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return table

    def add_evidence(self, evidence: SignalEvidence) -> None:
        stype = evidence.source_type
        if stype not in self._tables:
            return
        from datetime import datetime
        ts = datetime.fromtimestamp(evidence.timestamp).strftime("%H:%M:%S")
        table = self._tables[stype]

        values = self._format_evidence(stype, ts, evidence)
        row = table.rowCount()
        if row >= 500:
            table.removeRow(0)
            row = 499
        table.insertRow(row)
        for col, val in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(str(val) if val is not None else ""))
        table.scrollToBottom()

    def _format_evidence(self, stype: str, ts: str, e: SignalEvidence) -> list:
        if stype == "mqtt":
            return [ts, e.source_id, e.mqtt_topic or "", str(e.mqtt_payload_keys or "")]
        if stype == "mdns":
            return [ts, e.source_id, e.mdns_service_type or "", e.hostname or "", e.ip_address or ""]
        if stype == "ssdp":
            return [ts, e.source_id, e.ssdp_usn or "", e.ssdp_server or "", e.ip_address or ""]
        if stype == "nmap":
            return [ts, e.source_id, e.ip_address or "", e.hostname or "",
                    e.nmap_mac or "", e.nmap_vendor or "", e.nmap_os_guess or ""]
        return [ts, e.source_id, str(e)]
```

## Task 7.3 — MainWindow: add Page 2

Modify `main_window.py`:

1. Add import:
```python
from discover_client.gui.evidence_page import EvidencePage
from PySide6.QtWidgets import QStackedWidget
```

2. In `__init__`, replace the central widget setup:
```python
central = QWidget()
self.setCentralWidget(central)
root = QVBoxLayout(central)      # was QHBoxLayout — now wraps the stack
root.setContentsMargins(0, 0, 0, 0)
root.setSpacing(0)

# Page navigator bar
nav = QHBoxLayout()
nav.setObjectName("toolbar")
self._btn_page1 = QPushButton("Sources & Log")
self._btn_page1.setObjectName("navBtn")
self._btn_page1.clicked.connect(lambda: self._stack.setCurrentIndex(0))
self._btn_page2 = QPushButton("Evidence")
self._btn_page2.setObjectName("navBtn")
self._btn_page2.clicked.connect(lambda: self._stack.setCurrentIndex(1))
nav.addWidget(self._btn_page1)
nav.addWidget(self._btn_page2)
nav.addStretch()
root.addLayout(nav)

# Stack
self._stack = QStackedWidget()
root.addWidget(self._stack)

# Page 0: current layout (horizontal split: palette + right side)
page0 = QWidget()
page0_layout = QHBoxLayout(page0)      # wrap existing QHBoxLayout
page0_layout.setContentsMargins(0, 0, 0, 0)
page0_layout.setSpacing(0)
page0_layout.addWidget(self.palette)
page0_layout.addLayout(right)           # 'right' is the QVBoxLayout with toolbar+scroll+log
self._stack.addWidget(page0)

# Page 1: evidence
self._evidence_page = EvidencePage()
self._stack.addWidget(self._evidence_page)

# Worker: connect evidence signal
self.worker.evidence_produced.connect(self._on_evidence)
```

Wait — this is complex because the current layout builds `right` as a QVBoxLayout and then `root.addLayout(right, stretch=1)` where `root` is a QHBoxLayout. If I wrap page 0 in QStackedWidget, the layout nesting changes.

Simpler approach: instead of QStackedWidget, use QTabWidget at the root level? Or actually, the toolbar approach with QStackedWidget is cleaner for extensibility.

Let me think about the minimal change. Current:
```python
central = QWidget()
root = QHBoxLayout(central)    # palette | right
root.addWidget(palette)
# ... build 'right' QVBoxLayout ...
root.addLayout(right, stretch=1)
```

New:
```python
central = QWidget()
root = QVBoxLayout(central)

# Nav bar
nav = QHBoxLayout()
nav.setObjectName("toolbar")
... nav buttons ...
root.addLayout(nav)

# Stack
self._stack = QStackedWidget()
page0 = QWidget()
p0_layout = QHBoxLayout(page0)
p0_layout.setContentsMargins(0, 0, 0, 0)
p0_layout.setSpacing(0)
p0_layout.addWidget(self.palette)
p0_layout.addLayout(right)      # right is now added to p0_layout instead of root
self._stack.addWidget(page0)

self._evidence_page = EvidencePage()
self._stack.addWidget(self._evidence_page)
root.addWidget(self._stack)
```

And remove `root.addLayout(right, stretch=1)` since right is now in p0_layout.

And add `_on_evidence(evidence)` handler.

For the nav bar styling, add QSS for `#navBtn`.

3. Add `_on_evidence` method:
```python
def _on_evidence(self, evidence: SignalEvidence) -> None:
    self._evidence_page.add_evidence(evidence)
```

## Task 7.4 — QSS

Add to `style.qss`:
```css
#navBtn {
    background-color: rgb(44, 49, 58);
    border-radius: 4px;
    padding: 6px 16px;
    font-size: 10pt;
    font-weight: bold;
}
#navBtn:hover {
    background-color: rgb(55, 60, 70);
}

#evidenceTable {
    background-color: rgb(40, 44, 52);
    border: 1px solid rgb(44, 49, 58);
    gridline-color: rgb(44, 49, 58);
    font-size: 9pt;
}
#evidenceTable QHeaderView::section {
    background-color: rgb(33, 37, 43);
    color: rgb(189, 147, 249);
    border: none;
    border-bottom: 1px solid rgb(44, 49, 58);
    padding: 4px 8px;
    font-weight: bold;
}

QTabWidget::pane {
    border: 1px solid rgb(44, 49, 58);
    background-color: rgb(40, 44, 52);
}
QTabBar::tab {
    background-color: rgb(33, 37, 43);
    color: rgb(189, 147, 249);
    padding: 6px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: rgb(44, 49, 58);
    color: rgb(221, 221, 221);
}
```

## Verification

Start the app, add a source, click Start. Switch to Evidence page — each tab should show annotated events for that source type. MQTT tab shows topics and keys, NMAP tab shows IPs and MACs and vendor names (OUI-enriched).

Manual test script to run:
```python
# Verify EvidencePage creates correct tabs
from discover_client.gui.evidence_page import EvidencePage
page = EvidencePage()
assert page._tabs.count() == 4
assert page._tabs.tabText(0) == "MQTT"
assert page._tabs.tabText(1) == "MDNS"
assert page._tabs.tabText(2) == "SSDP"
assert page._tabs.tabText(3) == "NMAP"
print("EvidencePage tabs OK")

# Verify SignalEvidence flows through
from discover_client.identification.evidence import SignalEvidence
from discover_client.source import SourceEvent
from discover_client.identification import ANNOTATORS
annotator = ANNOTATORS["nmap"]()
event = SourceEvent(source_id="n-1", source_type="nmap", timestamp=1718234600.0,
    event_type="discovery", payload={
        "ip": "192.168.5.2", "mac": "58:04:4F:9A:DC:05",
        "vendor": "Unknown", "hostnames": ["58044F9ADC05.local."],
        "os_guess": "Linux", "status": "up"
    })
evidence = annotator.annotate(event)
assert evidence is not None
assert evidence.nmap_vendor == "TP-Link Systems Inc."  # OUI enriched
page.add_evidence(evidence)
assert page._tables["nmap"].rowCount() == 1
print("Evidence flow OK")
```
