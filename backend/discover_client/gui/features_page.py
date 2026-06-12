"""Semantic device features table."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from discover_client.gui.copyable_table import CopyableTableWidget
from discover_client.identification.features import DeviceFeatures


class FeaturesPage(QWidget):
    COLUMNS = [
        "Device ID",
        "Capabilities",
        "Protocols",
        "Vendor Hints",
        "Topic Prefixes",
        "Evidence Count",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._table = CopyableTableWidget(0, len(self.COLUMNS))
        self._table.setObjectName("featuresTable")
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(True)
        header = self._table.horizontalHeader()
        for index in range(len(self.COLUMNS) - 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._table)

    def set_features(self, features: list[DeviceFeatures]) -> None:
        self._table.setRowCount(0)
        for row, feature in enumerate(features):
            self._table.insertRow(row)
            values = [
                feature.device_id,
                ", ".join(feature.capabilities),
                ", ".join(feature.protocols),
                ", ".join(feature.vendor_hints),
                ", ".join(feature.topic_prefixes),
                str(feature.evidence_count),
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
