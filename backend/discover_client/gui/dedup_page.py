"""Deduplicated device table."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from discover_client.gui.main_window import CopyableTableWidget
from discover_client.identification.device import Device


class DedupPage(QWidget):
    COLUMNS = [
        "Device ID",
        "IPs",
        "Hostnames",
        "MAC Prefixes",
        "Vendor",
        "Service Types",
        "Topics",
        "Evidence Count",
        "Last Seen",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._table = CopyableTableWidget(0, len(self.COLUMNS))
        self._table.setObjectName("dedupTable")
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(True)
        header = self._table.horizontalHeader()
        for index in range(len(self.COLUMNS) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._table)

    def set_devices(self, devices: list[Device]) -> None:
        self._table.setRowCount(0)
        for row, device in enumerate(devices):
            self._table.insertRow(row)
            values = [
                device.device_id,
                ", ".join(sorted(device.ip_addresses)),
                ", ".join(sorted(device.hostnames)),
                ", ".join(sorted(device.mac_prefixes)),
                device.vendor or "",
                ", ".join(sorted(device.service_types)),
                ", ".join(sorted(device.topic_prefixes)),
                str(device.total_evidence_count),
                datetime.fromtimestamp(device.last_seen).strftime("%H:%M:%S") if device.last_seen else "",
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
