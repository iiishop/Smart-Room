"""Scrollable source card list for the Config tab."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SourceCard(QFrame):
    """One source card in the list. Click to select."""

    clicked = Signal(str, str, dict)

    def __init__(
        self,
        source_id: str,
        source_type: str,
        enabled: bool,
        settings: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sourceCard")
        self.source_id = source_id
        self.source_type = source_type
        self._settings = settings

        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        self.dot = QLabel("●" if enabled else "○")
        self.dot.setObjectName("statusDot")
        self.dot.setStyleSheet(
            f"color: {'#50fa7b' if enabled else '#6272a4'}; font-size: 16pt;"
        )
        layout.addWidget(self.dot)

        text = QVBoxLayout()
        type_label = QLabel(source_type.upper())
        type_label.setObjectName("cardTypeLabel")
        text.addWidget(type_label)
        id_label = QLabel(source_id)
        id_label.setStyleSheet("font-size: 12pt; background: transparent;")
        text.addWidget(id_label)
        layout.addLayout(text)

        layout.addStretch()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.source_id, self.source_type, self._settings)
        super().mousePressEvent(event)

    def update_enabled(self, enabled: bool) -> None:
        self.dot.setText("●" if enabled else "○")
        self.dot.setStyleSheet(
            f"color: {'#50fa7b' if enabled else '#6272a4'}; font-size: 16pt;"
        )


class SourceList(QScrollArea):
    """Scrollable list of SourceCards."""

    source_selected = Signal(str, str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sourceList")
        self.setWidgetResizable(True)
        self.setFixedWidth(260)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(2)

        title = QLabel("🔍 Sources")
        title.setObjectName("sourceListTitle")
        self._layout.addWidget(title)

        self._cards: dict[str, SourceCard] = {}

        self.btn_add = QPushButton("+ Add Source")
        self.btn_add.setObjectName("btnAdd")
        self._layout.addWidget(self.btn_add)

        self.setWidget(container)

    def set_sources(self, sources: list[dict[str, Any]]) -> None:
        for card in self._cards.values():
            self._layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        for src in sources:
            card = SourceCard(
                src["source_id"],
                src["source_type"],
                src.get("enabled", True),
                src.get("settings", {}),
            )
            card.clicked.connect(self._on_card_clicked)
            self._layout.insertWidget(self._layout.count() - 1, card)
            self._cards[src["source_id"]] = card

    def _on_card_clicked(self, source_id: str, source_type: str, settings: dict[str, Any]) -> None:
        self.source_selected.emit(source_id, source_type, settings)

    def update_status(self, source_id: str, enabled: bool) -> None:
        if source_id in self._cards:
            self._cards[source_id].update_enabled(enabled)
