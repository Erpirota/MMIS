"""Single-line QLabel that elides overflowing text on the right or in the middle."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """Single-line QLabel that elides overflowing text (middle or right)."""

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> None:
        if isinstance(parent, Qt.TextElideMode):
            mode = parent
            parent = None
        super().__init__(parent)
        self._raw_text = text
        self._mode = mode
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        self._raw_text = text
        self.setToolTip(text)
        self.update()

    def text(self) -> str:
        return self._raw_text

    def elide_mode(self) -> Qt.TextElideMode:
        return self._mode

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        if self._mode != mode:
            self._mode = mode
            self.update()

    def minimumSizeHint(self) -> QSize:
        return QSize(20, self.fontMetrics().height())

    def sizeHint(self) -> QSize:
        return QSize(20, self.fontMetrics().height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        fm = self.fontMetrics()
        elided = fm.elidedText(self._raw_text, self._mode, max(0, self.width()))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), elided)
