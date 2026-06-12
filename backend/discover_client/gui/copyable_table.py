"""QTableWidget subclass with Ctrl+C copy (tab-separated, paste-friendly)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QTableWidget


class CopyableTableWidget(QTableWidget):
    """QTableWidget with Ctrl+C copy (tab-separated, paste-friendly)."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self._copy_selected()
        else:
            super().keyPressEvent(event)

    def _copy_selected(self) -> None:
        rows: set[int] = set()
        cols: set[int] = set()
        for idx in self.selectedIndexes():
            rows.add(idx.row())
            cols.add(idx.column())

        if not rows:
            return

        sorted_cols = sorted(cols)
        lines: list[str] = []
        for r in sorted(rows):
            cells = []
            for c in sorted_cols:
                item = self.item(r, c)
                cells.append(item.text() if item else "")
            lines.append("\t".join(cells))

        QApplication.clipboard().setText("\n".join(lines))
