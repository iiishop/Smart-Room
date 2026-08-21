"""Evidence viewer with one tab per source type."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from discover_client.gui.copyable_table import CopyableTableWidget
from discover_client.identification.evidence import SignalEvidence


class EvidencePage(QWidget):
    COLUMNS = {
        "mqtt": ["Time", "Source", "Topic", "Payload Keys"],
        "mdns": ["Time", "Source", "Service Type", "Hostname", "IP"],
        "ssdp": ["Time", "Source", "USN", "Server", "IP"],
        "nmap": ["Time", "Source", "IP", "Hostname", "MAC", "Vendor", "OS"],
        "packet_sniff": ["Time", "Source", "Client ID", "Topic", "IP", "MAC"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tables: dict[str, CopyableTableWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        for source_type, columns in self.COLUMNS.items():
            table = self._make_table(columns)
            self._tables[source_type] = table
            self._tabs.addTab(table, source_type.upper())

    def _make_table(self, columns: list[str]) -> CopyableTableWidget:
        table = CopyableTableWidget(0, len(columns))
        table.setObjectName("evidenceTable")
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setStretchLastSection(True)
        header = table.horizontalHeader()
        for index in range(len(columns) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        return table

    def add_evidence(self, evidence: SignalEvidence) -> None:
        table = self._tables.get(evidence.source_type)
        if table is None:
            return

        ts = datetime.fromtimestamp(evidence.timestamp).strftime("%H:%M:%S")
        values = self._format_evidence(evidence.source_type, ts, evidence)

        row = table.rowCount()
        if row >= 500:
            table.removeRow(0)
            row = 499
        table.insertRow(row)
        for col, value in enumerate(values):
            table.setItem(row, col, QTableWidgetItem(str(value) if value is not None else ""))
        table.scrollToBottom()

    def add_evidence_batch(self, evidences: list[SignalEvidence]) -> None:
        """Bulk-insert evidence rows, blocking visual updates until done."""
        if not evidences:
            return
        # All evidence in a batch share the same source_type
        table = self._tables.get(evidences[0].source_type)
        if table is None:
            return

        table.setUpdatesEnabled(False)
        for evidence in evidences:
            ts = datetime.fromtimestamp(evidence.timestamp).strftime("%H:%M:%S")
            values = self._format_evidence(evidence.source_type, ts, evidence)
            row = table.rowCount()
            if row >= 500:
                table.removeRow(0)
                row = 499
            table.insertRow(row)
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value) if value is not None else ""))
        table.setUpdatesEnabled(True)
        table.scrollToBottom()

    def _format_evidence(self, source_type: str, ts: str, evidence: SignalEvidence) -> list[str]:
        if source_type == "mqtt":
            keys = sorted(evidence.mqtt_payload_keys or [])
            return [ts, evidence.source_id, evidence.mqtt_topic or "", ", ".join(keys)]
        if source_type == "mdns":
            return [
                ts,
                evidence.source_id,
                evidence.mdns_service_type or "",
                evidence.hostname or "",
                evidence.ip_address or "",
            ]
        if source_type == "ssdp":
            return [
                ts,
                evidence.source_id,
                evidence.ssdp_usn or "",
                evidence.ssdp_server or "",
                evidence.ip_address or "",
            ]
        if source_type == "nmap":
            return [
                ts,
                evidence.source_id,
                evidence.ip_address or "",
                evidence.hostname or "",
                evidence.nmap_mac or "",
                evidence.nmap_vendor or "",
                evidence.nmap_os_guess or "",
            ]
        if source_type == "packet_sniff":
            return [
                ts,
                evidence.source_id,
                evidence.mqtt_client_id or "",
                evidence.mqtt_topic or "",
                evidence.ip_address or "",
                evidence.nmap_mac or "",
            ]
        return [ts, evidence.source_id, evidence.summarize()]
