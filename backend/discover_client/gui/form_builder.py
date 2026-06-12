"""Auto-generate settings forms from source type schemas."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from discover_client.config import SCHEMAS

FIELD_MAP: dict[str, tuple[Any, ...]] = {
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


def _create_widget(key: str, current_value: Any) -> QWidget:
    """Create the appropriate widget for a given settings key."""
    if key in FIELD_MAP:
        spec = FIELD_MAP[key]
        cls = spec[0]
        args = spec[1:]
        if cls is QLineEdit:
            widget = QLineEdit()
            if args == ("password",):
                widget.setEchoMode(QLineEdit.EchoMode.Password)
            return widget
        if cls is QSpinBox:
            widget = QSpinBox()
            if len(args) >= 2:
                widget.setRange(args[0], args[1])
            return widget
        return cls(*args)

    if isinstance(current_value, int):
        widget = QSpinBox()
        widget.setRange(0, 999999)
        return widget
    if isinstance(current_value, list):
        return QTextEdit()
    return QLineEdit()


def _set_widget_value(widget: QWidget, value: Any) -> None:
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


def _get_widget_value(widget: QWidget) -> Any:
    """Read the current value from a form widget."""
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QTextEdit):
        text = widget.toPlainText().strip()
        return text.splitlines() if text else []
    return None


class FormBuilder(QGroupBox):
    """Builds a settings form dynamically from a source schema."""

    saved = Signal(str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Settings", parent)
        self._form = QFormLayout(self)
        self._widgets: dict[str, QWidget] = {}
        self._source_id = ""
        self._source_type = ""

        self._btn_save = QPushButton("Save")
        self._btn_save.clicked.connect(self._on_save)
        self._form.addRow(self._btn_save)

    def load_source(self, source_id: str, source_type: str, settings: dict[str, Any]) -> None:
        """Rebuild the form for the given source type and settings."""
        while self._form.rowCount() > 1:
            self._form.removeRow(0)
        self._widgets.clear()

        self._source_id = source_id
        self._source_type = source_type

        schema = SCHEMAS.get(source_type)
        if schema is None:
            self._form.insertRow(0, QLabel(f"Unknown source type: {source_type}"))
            return

        for key in schema.defaults:
            current = settings.get(key, schema.defaults[key])
            widget = _create_widget(key, current)
            _set_widget_value(widget, current)
            self._form.insertRow(self._form.rowCount() - 1, key.replace("_", " ").title(), widget)
            self._widgets[key] = widget

    def _on_save(self) -> None:
        settings: dict[str, Any] = {}
        for key, widget in self._widgets.items():
            settings[key] = _get_widget_value(widget)
        self.saved.emit(self._source_id, settings)
