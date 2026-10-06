"""Circular progress and health metric ring widget drawn with QPainter."""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPen, QPainter
from PySide6.QtWidgets import QWidget

from mmis.ui import strings
from mmis.ui.theme import tokens


class MetricRing(QWidget):
    """Circular health score indicator ring."""

    def __init__(
        self,
        score: int = 100,
        label: str = strings.DASHBOARD_HEALTH_LABEL,
        status: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._score = max(0, min(100, score))
        self._label = label
        self._status = status
        self.setMinimumSize(QSize(130, 130))

    def set_score(self, score: int, status: str | None = None) -> None:
        self._score = max(0, min(100, score))
        if status is not None:
            self._status = status
        self.update()

    def set_status(self, status: str | None) -> None:
        self._status = status
        self.update()

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def get_status_text_geometry(self) -> tuple[QRectF, QFont, float]:
        """Return (status_rect, font, chord_width) used for drawing status text.

        chord_width is the available horizontal width of the inner clear circle
        at the vertical center of status_rect.
        """
        size = min(self.width(), self.height())
        stroke_width = 10
        margin = stroke_width / 2 + 6
        rect = QRectF(
            (self.width() - size) / 2 + margin,
            (self.height() - size) / 2 + margin,
            size - 2 * margin,
            size - 2 * margin,
        )
        status_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.67, rect.width(), rect.height() * 0.18)

        status_font = QFont(self.font())
        status_font.setPointSize(tokens.FONT_SIZE_XS)
        status_font.setWeight(QFont.Weight.Bold)

        inner_radius = (rect.width() - stroke_width) / 2.0
        cy = self.height() / 2.0
        d_y = abs(status_rect.center().y() - cy)
        chord_width = 2.0 * math.sqrt(max(0.0, inner_radius**2 - d_y**2))

        return status_rect, status_font, chord_width

    def get_color(self) -> str:
        if self._status:
            status_clean = self._status.strip().lower()
            if status_clean == "errors":
                return tokens.COLOR_ERROR
            if status_clean == "warnings":
                return tokens.COLOR_WARNING
            if status_clean == "healthy":
                return tokens.COLOR_SUCCESS
        if self._score >= 85:
            return tokens.COLOR_SUCCESS
        if self._score >= 60:
            return tokens.COLOR_WARNING
        return tokens.COLOR_ERROR

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = min(self.width(), self.height())
        stroke_width = 10
        margin = stroke_width / 2 + 6
        rect = QRectF(
            (self.width() - size) / 2 + margin,
            (self.height() - size) / 2 + margin,
            size - 2 * margin,
            size - 2 * margin,
        )

        # Draw background track circle
        track_pen = QPen(QColor(tokens.SURFACE_2), stroke_width)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)

        # Draw active value arc (angles in 1/16th of a degree; 90 degrees starts at 12 o'clock)
        start_angle = 90 * 16
        span_angle = -int(360 * 16 * (self._score / 100.0))

        active_pen = QPen(QColor(self.get_color()), stroke_width)
        active_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(active_pen)
        painter.drawArc(rect, start_angle, span_angle)

        # Draw center score percentage text
        score_font = painter.font()
        score_font.setPointSize(tokens.FONT_SIZE_XL)
        score_font.setWeight(QFont.Weight.Bold)
        painter.setFont(score_font)
        painter.setPen(QColor(tokens.TEXT_PRIMARY))

        text_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.20, rect.width(), rect.height() * 0.32)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, f"{self._score}%")

        # Draw label ("Health" / metric name)
        label_font = painter.font()
        label_font.setPointSize(tokens.FONT_SIZE_XS)
        label_font.setWeight(QFont.Weight.Medium)
        painter.setFont(label_font)
        painter.setPen(QColor(tokens.TEXT_SECONDARY))

        subtext_rect = QRectF(rect.x(), rect.y() + rect.height() * 0.50, rect.width(), rect.height() * 0.20)
        painter.drawText(subtext_rect, Qt.AlignmentFlag.AlignCenter, self._label)

        # Draw status word separately if set (e.g. "Errors" or "Healthy")
        if self._status:
            status_rect, status_font, _ = self.get_status_text_geometry()
            painter.setFont(status_font)
            painter.setPen(QColor(self.get_color()))
            painter.drawText(status_rect, Qt.AlignmentFlag.AlignCenter, self._status)

