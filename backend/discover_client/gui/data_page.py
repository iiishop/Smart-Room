"""Latest sensor values grouped by deduplicated device."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from discover_client.gui.copyable_table import CopyableTableWidget
from discover_client.identification.data_snapshot import SensorReading
from discover_client.identification.device import Device


class DataPage(QWidget):
    COLUMNS = ["Device ID", "Sensor", "Latest Value", "Unit", "Last Updated"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices: list[Device] = []
        self._snapshot: dict[str, dict[str, list[SensorReading]]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._table = CopyableTableWidget(0, len(self.COLUMNS))
        self._table.setObjectName("dataTable")
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
        self._devices = list(devices)
        self._render()

    def set_snapshot(self, snapshot: dict[str, dict[str, list[SensorReading]]]) -> None:
        self._snapshot = snapshot
        self._render()

    def _render(self) -> None:
        self._table.setRowCount(0)
        row = 0
        seen_device_ids: set[str] = set()

        for device in self._devices:
            seen_device_ids.add(device.device_id)
            latest = _latest_by_sensor(self._snapshot.get(device.device_id, {}))
            if not latest:
                self._insert_row(row, device.device_id, "(no data)", "-", "-", "-")
                row += 1
                continue

            for sensor_type, reading in sorted(latest.items()):
                self._insert_row(
                    row,
                    device.device_id,
                    sensor_type,
                    f"{reading.value:g}",
                    reading.unit or "",
                    datetime.fromtimestamp(reading.timestamp).strftime("%H:%M:%S"),
                )
                row += 1

        for device_id in sorted(self._snapshot):
            if device_id in seen_device_ids:
                continue
            for sensor_type, reading in sorted(_latest_by_sensor(self._snapshot[device_id]).items()):
                self._insert_row(
                    row,
                    device_id,
                    sensor_type,
                    f"{reading.value:g}",
                    reading.unit or "",
                    datetime.fromtimestamp(reading.timestamp).strftime("%H:%M:%S"),
                )
                row += 1

    def _insert_row(self, row: int, device_id: str, sensor: str, value: str, unit: str, updated: str) -> None:
        self._table.insertRow(row)
        for column, cell_value in enumerate([device_id, sensor, value, unit, updated]):
            self._table.setItem(row, column, QTableWidgetItem(cell_value))


def _latest_by_sensor(snapshot: dict[str, list[SensorReading]]) -> dict[str, SensorReading]:
    return {
        sensor_type: readings[-1]
        for sensor_type, readings in snapshot.items()
        if readings
    }
