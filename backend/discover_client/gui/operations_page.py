"""Operations discovered from MQTT command topics."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from discover_client.gui.copyable_table import CopyableTableWidget
from discover_client.operations import OperationCapability


class OperationsPage(QWidget):
    COLUMNS = ["Device ID", "Topic", "Action", "Values", "Confidence", "First Seen", "Last Seen"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capabilities: dict[str, list[OperationCapability]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._placeholder = QLabel("(no operations discovered)")
        self._placeholder.setObjectName("operationsPlaceholder")
        layout.addWidget(self._placeholder)

        self._table = CopyableTableWidget(0, len(self.COLUMNS))
        self._table.setObjectName("operationsTable")
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(True)
        header = self._table.horizontalHeader()
        for index in range(len(self.COLUMNS) - 2):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.hide()
        layout.addWidget(self._table)

    def set_capabilities(self, device_id: str, capabilities: list[OperationCapability]) -> None:
        if capabilities:
            self._capabilities[device_id] = list(capabilities)
        else:
            self._capabilities.pop(device_id, None)
        self._render()

    def _render(self) -> None:
        all_capabilities: list[OperationCapability] = []
        for device_id in sorted(self._capabilities):
            all_capabilities.extend(sorted(self._capabilities[device_id], key=lambda cap: cap.topic))

        self._table.setRowCount(0)
        has_rows = bool(all_capabilities)
        self._placeholder.setVisible(not has_rows)
        self._table.setVisible(has_rows)
        if not has_rows:
            return

        for row, capability in enumerate(all_capabilities):
            self._table.insertRow(row)
            values = [
                capability.device_id,
                capability.topic,
                capability.action,
                ", ".join(capability.accepted_values) if capability.accepted_values else "-",
                f"{capability.confidence:.2f}",
                datetime.fromtimestamp(capability.first_seen).strftime("%H:%M:%S"),
                datetime.fromtimestamp(capability.last_seen).strftime("%H:%M:%S"),
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
