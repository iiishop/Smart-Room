"""Card-based device profiles that combine device, data, and operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


@dataclass
class DeviceProfile:
    device_id: str
    category: str
    confidence: float
    ip_addresses: set[str] = field(default_factory=set)
    mac_prefixes: set[str] = field(default_factory=set)
    vendor: str | None = None
    data_sensors: dict[str, dict] = field(default_factory=dict)
    operations: list[dict] = field(default_factory=list)
    total_evidence_count: int = 0
    last_seen: float = 0.0


class DeviceProfileCard(QFrame):
    publish_requested = Signal(str, object)

    def __init__(self, profile: DeviceProfile, parent=None):
        super().__init__(parent)
        self.setObjectName("deviceProfileCard")
        self._profile = profile
        self._expanded = False
        self._data_labels: dict[str, QLabel] = {}
        self._operation_rows: dict[str, QFrame] = {}
        self._operation_inputs: dict[str, dict[str, QLineEdit]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._header = QFrame()
        self._header.setObjectName("deviceProfileHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        dot = QLabel("●")
        dot.setObjectName("deviceProfileDot")
        header_layout.addWidget(dot)

        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(2)

        self._title_label = QLabel(profile.device_id)
        self._title_label.setObjectName("deviceProfileTitle")
        title_column.addWidget(self._title_label)

        self._subtitle_label = QLabel(profile.vendor or "Unknown device")
        self._subtitle_label.setObjectName("deviceProfileSubtitle")
        title_column.addWidget(self._subtitle_label)
        header_layout.addLayout(title_column, stretch=1)

        self._evidence_label = QLabel(f"{profile.total_evidence_count} evidence")
        self._evidence_label.setObjectName("deviceProfileEvidence")
        header_layout.addWidget(self._evidence_label)

        self._toggle_label = QLabel("Expand")
        self._toggle_label.setObjectName("deviceProfileToggle")
        header_layout.addWidget(self._toggle_label)

        root.addWidget(self._header)

        ip_text = ", ".join(sorted(profile.ip_addresses)) or "-"
        mac_text = ", ".join(sorted(profile.mac_prefixes)) or "-"
        self._identity_label = QLabel(f"IP: {ip_text}    MAC: {mac_text}")
        self._identity_label.setObjectName("deviceProfileIdentity")
        root.addWidget(self._identity_label)

        self._meta_label = QLabel(f"{profile.category} | Confidence: {profile.confidence:.2f}")
        self._meta_label.setObjectName("deviceProfileMeta")
        root.addWidget(self._meta_label)

        self._details = QFrame()
        self._details.setObjectName("deviceProfileDetails")
        details_layout = QVBoxLayout(self._details)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(8)

        data_section = QFrame()
        data_section.setObjectName("deviceProfileSection")
        data_section_layout = QVBoxLayout(data_section)
        data_section_layout.setContentsMargins(10, 10, 10, 10)
        data_section_layout.setSpacing(6)
        data_title = QLabel("Data")
        data_title.setObjectName("deviceProfileSectionTitle")
        data_section_layout.addWidget(data_title)
        self._data_layout = QVBoxLayout()
        self._data_layout.setContentsMargins(0, 0, 0, 0)
        self._data_layout.setSpacing(4)
        data_section_layout.addLayout(self._data_layout)
        details_layout.addWidget(data_section)

        ops_section = QFrame()
        ops_section.setObjectName("deviceProfileSection")
        ops_section_layout = QVBoxLayout(ops_section)
        ops_section_layout.setContentsMargins(10, 10, 10, 10)
        ops_section_layout.setSpacing(6)
        ops_title = QLabel("Operations")
        ops_title.setObjectName("deviceProfileSectionTitle")
        ops_section_layout.addWidget(ops_title)
        self._operations_layout = QVBoxLayout()
        self._operations_layout.setContentsMargins(0, 0, 0, 0)
        self._operations_layout.setSpacing(6)
        ops_section_layout.addLayout(self._operations_layout)
        details_layout.addWidget(ops_section)

        root.addWidget(self._details)
        self._details.hide()

        self._render_data()
        self._render_operations()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_expanded(not self._expanded)
        super().mousePressEvent(event)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._details.setVisible(expanded)
        self._toggle_label.setText("Collapse" if expanded else "Expand")

    def update(self, profile: DeviceProfile) -> None:
        self._profile = profile
        self._title_label.setText(profile.device_id)
        self._subtitle_label.setText(profile.vendor or "Unknown device")
        self._evidence_label.setText(f"{profile.total_evidence_count} evidence")
        ip_text = ", ".join(sorted(profile.ip_addresses)) or "-"
        mac_text = ", ".join(sorted(profile.mac_prefixes)) or "-"
        self._identity_label.setText(f"IP: {ip_text}    MAC: {mac_text}")
        self._meta_label.setText(f"{profile.category} | Confidence: {profile.confidence:.2f}")
        self._render_data()
        self._render_operations()

    def _render_data(self) -> None:
        incoming = set(self._profile.data_sensors.keys()) if self._profile.data_sensors else set()
        existing = set(self._data_labels.keys())

        # Remove labels for sensors that disappeared
        for removed in existing - incoming:
            label = self._data_labels.pop(removed)
            self._data_layout.removeWidget(label)
            label.deleteLater()

        if not self._profile.data_sensors:
            # Show placeholder only if there's no data and no existing labels
            if not self._data_labels:
                placeholder = QLabel("(no data)")
                self._data_layout.addWidget(placeholder)
            return

        # Remove placeholder if data exists
        _remove_first_placeholder(self._data_layout)

        for sensor_name in sorted(self._profile.data_sensors):
            sensor = self._profile.data_sensors[sensor_name]
            text = f"{sensor_name}: {sensor.get('value', '')}{sensor.get('unit', '')}"
            if sensor_name in self._data_labels:
                self._data_labels[sensor_name].setText(text)
            else:
                label = QLabel(text)
                label.setObjectName("deviceProfileDataItem")
                self._data_labels[sensor_name] = label
                self._data_layout.addWidget(label)

    def _render_operations(self) -> None:
        incoming_ops = self._profile.operations or []
        incoming_keys = {_op_key(op) for op in incoming_ops}
        existing_keys = set(self._operation_rows.keys())

        # Remove rows for operations that disappeared
        for removed_key in existing_keys - incoming_keys:
            row = self._operation_rows.pop(removed_key)
            self._operation_inputs.pop(removed_key, None)
            self._operations_layout.removeWidget(row)
            row.deleteLater()

        # Show placeholder only if there are no ops and no existing rows
        if not incoming_ops:
            if not self._operation_rows:
                placeholder = QLabel("(no operations)")
                self._operations_layout.addWidget(placeholder)
            return

        # Remove placeholder if operations exist
        _remove_first_placeholder(self._operations_layout)

        for operation in incoming_ops:
            key = _op_key(operation)
            if key in self._operation_rows:
                # Row already exists — just update text that might have changed
                row = self._operation_rows[key]
                _update_op_row_secondary(row, operation)
            else:
                row, inputs = _build_op_row(operation, self.publish_requested)
                self._operation_rows[key] = row
                self._operation_inputs[key] = inputs
                self._operations_layout.addWidget(row)


class DeviceProfilePage(QWidget):
    publish_requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict[str, DeviceProfileCard] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll)

        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(10)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(container)

    def set_profiles(self, profiles: list[DeviceProfile]) -> None:
        incoming_ids = {p.device_id for p in profiles}
        existing_ids = set(self._cards.keys())

        # Remove cards for devices no longer present
        for removed_id in existing_ids - incoming_ids:
            card = self._cards.pop(removed_id)
            self._cards_layout.removeWidget(card)
            card.deleteLater()

        # Update existing cards and add new ones
        for profile in profiles:
            if profile.device_id in self._cards:
                self._cards[profile.device_id].update(profile)
            else:
                card = DeviceProfileCard(profile)
                card.publish_requested.connect(self.publish_requested.emit)
                self._cards[profile.device_id] = card
                self._cards_layout.addWidget(card)


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count() > 0:
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _op_key(operation: dict) -> str:
    topic = str(operation.get("topic") or "")
    action = str(operation.get("action") or "")
    return f"{topic}::{action}"


def _build_op_row(operation: dict, signal: Signal) -> tuple[QFrame, dict[str, QLineEdit]]:
    row = QFrame()
    row.setObjectName("deviceProfileOperationRow")
    row_layout = QVBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setSpacing(4)

    controls = QHBoxLayout()
    controls.setContentsMargins(0, 0, 0, 0)
    controls.setSpacing(6)
    row_layout.addLayout(controls)

    args = operation.get("args", [])
    inputs: dict[str, QLineEdit] = {}
    if args:
        for arg in args:
            field = QLineEdit()
            field.setPlaceholderText(str(arg.get("example") or arg.get("key") or "value"))
            field.setObjectName("deviceProfileInput")
            controls.addWidget(field)
            inputs[str(arg.get("key") or "value")] = field
        button = QPushButton(str(operation.get("action") or "Send"))
        button.clicked.connect(
            lambda _checked=False, op=operation, arg_inputs=inputs: signal.emit(
                str(op.get("topic") or ""),
                {key: widget.text() for key, widget in arg_inputs.items()},
            )
        )
        controls.addWidget(button)
    else:
        button = QPushButton(str(operation.get("action") or "Send"))
        button.clicked.connect(
            lambda _checked=False, op=operation: signal.emit(
                str(op.get("topic") or ""),
                _payload_for_operation(op),
            )
        )
        controls.addWidget(button)

    topic = str(operation.get("topic") or "")
    topic_label = QLabel(f"topic: {topic}   payload: {json.dumps(_preview_payload(operation), ensure_ascii=True)}")
    topic_label.setObjectName("deviceProfileOperationMeta")
    topic_label.setWordWrap(True)
    row_layout.addWidget(topic_label)

    # Stash references for incremental update
    row._button = button  # type: ignore[attr-defined]
    row._topic_label = topic_label  # type: ignore[attr-defined]

    return row, inputs


def _update_op_row_secondary(row: QFrame, operation: dict) -> None:
    """Update the button text and topic label of an existing operation row without touching QLineEdits."""
    button: QPushButton = row._button  # type: ignore[attr-defined]
    button.setText(str(operation.get("action") or "Send"))

    topic_label: QLabel = row._topic_label  # type: ignore[attr-defined]
    topic = str(operation.get("topic") or "")
    topic_label.setText(f"topic: {topic}   payload: {json.dumps(_preview_payload(operation), ensure_ascii=True)}")


def _remove_first_placeholder(layout: QVBoxLayout) -> None:
    """If the first widget in the layout is a '(no data)' or '(no operations)' label, remove it."""
    if layout.count() == 0:
        return
    item = layout.itemAt(0)
    if item is None:
        return
    widget = item.widget()
    if widget is None:
        return
    text = widget.property("text")
    if isinstance(text, str) and text.startswith("(no "):
        layout.removeWidget(widget)
        widget.deleteLater()


def _payload_for_operation(operation: dict) -> object:
    values = operation.get("accepted_values") or []
    if values:
        return values[0]
    return {}


def _preview_payload(operation: dict) -> object:
    args = operation.get("args") or []
    if args:
        return {str(arg.get("key") or "value"): arg.get("example") or "" for arg in args}
    return _payload_for_operation(operation)
