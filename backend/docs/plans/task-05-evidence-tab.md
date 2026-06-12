# Task 05 — Evidence Tab (Annotator Results)

## Scope
Add a second tab to the GUI showing annotator output in real-time. Currently the right side has a single view (source cards + log table). Wrap it in QTabWidget: "Discover" (existing) + "Evidence" (new).

## Design

```
┌──────────────────────────────────────────────┐
│ [Discover] [Evidence]                        │
├──────────────────────────────────────────────┤
│ Tab "Discover":                              │
│   ┌── source cards (grid, scrollable) ──┐    │
│   └──────────────────────────────────────┘    │
│   ┌── global event log ─────────────────┐    │
│   │ Time | Source | Event               │    │
│   └──────────────────────────────────────┘    │
│                                              │
│ Tab "Evidence":                              │
│   ┌── annotator output table ───────────┐    │
│   │ Time | Source | Annotator | Clues   │    │
│   └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

## Files to modify
- `discover_client/gui/main_window.py`
- `discover_client/identification/evidence.py` — add `summarize()` method
- `discover_client/gui/style.qss` — add `#evidenceTable` styles

## Task 5.1 — SignalEvidence.summarize()

Add a `summarize()` method to `SignalEvidence` that returns a human-readable one-liner of the key clues. Handle the variety of fields across annotator types:

```python
def summarize(self) -> str:
    parts = []
    if self.ip_address:
        parts.append(f"ip={self.ip_address}")
    if self.hostname:
        parts.append(f"host={self.hostname}")
    if self.mac_prefix:
        parts.append(f"mac={self.mac_prefix}")
    if self.nmap_vendor:
        parts.append(f"vendor={self.nmap_vendor}")
    if self.nmap_os_guess:
        parts.append(f"os={self.nmap_os_guess}")
    if self.mqtt_topic:
        parts.append(f"topic={self.mqtt_topic}")
    if self.mqtt_payload_keys:
        parts.append(f"keys={{{','.join(sorted(self.mqtt_payload_keys))}}}")
    if self.mdns_service_type:
        parts.append(f"svc={self.mdns_service_type}")
    if self.mdns_txt_keys:
        parts.append(f"txt={{{','.join(sorted(self.mdns_txt_keys))}}}")
    if self.ssdp_usn:
        parts.append(f"usn={self.ssdp_usn[:60]}")
    if self.ssdp_server:
        parts.append(f"srv={self.ssdp_server}")
    return "  ".join(parts) if parts else "(no clues)"
```

## Task 5.2 — Wrap right side in QTabWidget

Replace the right-side VBoxLayout structure with QTabWidget containing two tabs.

```python
# Expected init code pattern:
self._tabs = QTabWidget()
self._tabs.setObjectName("mainTabs")

# Tab 1: Discover
discover_tab = QWidget()
discover_layout = QVBoxLayout(discover_tab)
discover_layout.setContentsMargins(8, 8, 8, 8)
discover_layout.addLayout(toolbar)
discover_layout.addWidget(self.scroll, stretch=1)
discover_layout.addWidget(self._log_table, stretch=2)
self._tabs.addTab(discover_tab, "Discover")

# Tab 2: Evidence
evidence_tab = QWidget()
evidence_layout = QVBoxLayout(evidence_tab)
evidence_layout.setContentsMargins(8, 8, 8, 8)

self._evidence_table = CopyableTableWidget(0, 4)
self._evidence_table.setObjectName("evidenceTable")
self._evidence_table.setHorizontalHeaderLabels(["Time", "Source", "Annotator", "Key Clues"])
self._evidence_table.horizontalHeader().setStretchLastSection(True)
self._evidence_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
self._evidence_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
self._evidence_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
self._evidence_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
self._evidence_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
evidence_layout.addWidget(self._evidence_table)
self._tabs.addTab(evidence_tab, "Evidence")

right.addWidget(self._tabs)
```

## Task 5.3 — Hook annotators into _on_event

After the existing log table append, run ALL registered annotators against the SourceEvent and populate the evidence table:

```python
# In _on_event, after log table append:
# Run annotators
from discover_client.identification import ANNOTATORS

annotator = ANNOTATORS.get(source_type)
if annotator:
    evidence = annotator().annotate(event)
    if evidence:
        row = self._evidence_table.rowCount()
        if row >= 500:
            self._evidence_table.removeRow(0)
            row = 499
        self._evidence_table.insertRow(row)
        self._evidence_table.setItem(row, 0, QTableWidgetItem(ts))
        self._evidence_table.setItem(row, 1, QTableWidgetItem(source_id))
        self._evidence_table.setItem(row, 2, QTableWidgetItem(source_type.upper()))
        self._evidence_table.setItem(row, 3, QTableWidgetItem(evidence.summarize()))
        self._evidence_table.scrollToBottom()
```

Note: need to also import QTabWidget at the top of main_window.py.

## Task 5.4 — QSS for evidence table

Add `#evidenceTable` styles to style.qss — identical to `#globalLogTable` styles but with `evidenceTable` objectName:

```css
#evidenceTable {
    background-color: rgb(40, 44, 52);
    border: 1px solid rgb(44, 49, 58);
    border-radius: 4px;
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
```

Also add minimal QTabWidget styles:

```css
QTabWidget::pane {
    border: 1px solid rgb(44, 49, 58);
    background-color: rgb(40, 44, 52);
}
QTabBar::tab {
    background-color: rgb(33, 37, 43);
    color: rgb(120, 125, 145);
    padding: 8px 20px;
    border: none;
    border-right: 1px solid rgb(44, 49, 58);
}
QTabBar::tab:selected {
    color: rgb(189, 147, 249);
    border-bottom: 2px solid rgb(189, 147, 249);
}
QTabBar::tab:hover {
    color: rgb(221, 221, 221);
}
```

## Verification

Run the GUI, add sources, start them. Switch to Evidence tab — should see annotator output rows appearing. Test:

```python
from discover_client.identification.evidence import SignalEvidence
e = SignalEvidence(source_id="test", source_type="mqtt", mqtt_topic="govee/+/state", mqtt_payload_keys={"temp"})
s = e.summarize()
assert "topic=govee/+/state" in s
assert "keys={temp}" in s

e2 = SignalEvidence(source_id="test", source_type="nmap", nmap_mac="AA:BB:CC", nmap_vendor="Intel", ip_address="192.168.1.1")
s2 = e2.summarize()
assert "mac=AA:BB:CC" in s2
assert "vendor=Intel" in s2
assert "ip=192.168.1.1" in s2
print("summarize() checks passed")
```
