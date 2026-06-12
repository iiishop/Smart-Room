"""Add / Edit source configuration dialog — auto-generated from SCHEMAS."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDialogButtonBox,
)

from discover_client.config import SCHEMAS

# --- widget factory (same as before) ---

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


def _make_widget(key: str, current: Any) -> QWidget:
    if key in FIELD_MAP:
        spec = FIELD_MAP[key]
        cls = spec[0]
        args = spec[1:]
        if cls is QSpinBox:
            w = QSpinBox()
            if len(args) >= 2:
                w.setRange(args[0], args[1])
            return w
        if cls is QLineEdit:
            w = QLineEdit()
            if args == ("password",):
                w.setEchoMode(QLineEdit.EchoMode.Password)
            return w
        return cls()
    if isinstance(current, int):
        w = QSpinBox()
        w.setRange(0, 999999)
        return w
    if isinstance(current, list):
        return QTextEdit()
    return QLineEdit()


def _set_value(widget: QWidget, value: Any) -> None:
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


def _get_value(widget: QWidget) -> Any:
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QTextEdit):
        text = widget.toPlainText().strip()
        return text.splitlines() if text else []
    return None


# --- dialog ---

class SourceDialog(QDialog):
    """Modal dialog for adding or editing a source configuration."""

    saved = Signal(str, str, dict)  # source_id, source_type, settings

    def __init__(
        self,
        source_type: str,
        parent: QWidget | None = None,
        edit_source_id: str = "",
        edit_settings: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sourceDialog")
        self._source_type = source_type
        self._edit_source_id = edit_source_id
        self._widgets: dict[str, QWidget] = {}

        title = f"Edit {edit_source_id}" if edit_source_id else f"Add {source_type.upper()} Source"
        self.setWindowTitle(title)
        self.resize(450, 400)

        root = QVBoxLayout(self)

        form = QFormLayout()
        self._build_form(form, edit_settings or {})
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_form(self, form: QFormLayout, current: dict[str, Any]) -> None:
        schema = SCHEMAS.get(self._source_type)
        if schema is None:
            form.addRow(QLabel(f"Unknown source type: {self._source_type}"))
            return

        # source_id row (only when adding new)
        if not self._edit_source_id:
            self._id_widget = QLineEdit()
            self._id_widget.setPlaceholderText(f"my-{self._source_type}")
            form.addRow("Source ID", self._id_widget)

        for key in schema.defaults:
            value = current.get(key, schema.defaults[key])
            w = _make_widget(key, value)
            _set_value(w, value)
            form.addRow(key.replace("_", " ").title(), w)
            self._widgets[key] = w

    def _on_ok(self) -> None:
        source_id = self._edit_source_id
        if not source_id:
            source_id = self._id_widget.text().strip()
            if not source_id:
                return

        settings: dict[str, Any] = {}
        for key, widget in self._widgets.items():
            settings[key] = _get_value(widget)

        self.saved.emit(source_id, self._source_type, settings)
        self.accept()
