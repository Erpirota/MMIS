"""Status pill indicator and QTableView item delegate using pure QPainter."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QStyledItemDelegate, QWidget

from mmis.ui import strings
from mmis.ui.theme import tokens

STATUS_CONFIG = {
    "ok": {
        "text": strings.MODS_STATUS_OK,
        "color": tokens.COLOR_SUCCESS,
    },
    "warning": {
        "text": strings.MODS_STATUS_WARNING,
        "color": tokens.COLOR_WARNING,
    },
    "error": {
        "text": strings.MODS_STATUS_ERROR,
        "color": tokens.COLOR_ERROR,
    },
    "disabled": {
        "text": strings.MODS_STATUS_DISABLED,
        "color": tokens.COLOR_DISABLED,
    },
    "info": {
        "text": "Info",
        "color": tokens.COLOR_INFO,
    },
    # Diff change statuses
    "added": {
        "text": "Added",
        "color": tokens.COLOR_SUCCESS,
    },
    "removed": {
        "text": "Removed",
        "color": tokens.COLOR_ERROR,
    },
    "changed": {
        "text": "Changed",
        "color": tokens.COLOR_WARNING,
    },
    "enabled": {
        "text": "Enabled",
        "color": tokens.COLOR_SUCCESS,
    },
    # Profile 1:1 statuses
    "healthy": {
        "text": "Healthy",
        "color": tokens.COLOR_SUCCESS,
    },
    "warnings": {
        "text": "Warnings",
        "color": tokens.COLOR_WARNING,
    },
    "errors": {
        "text": "Errors",
        "color": tokens.COLOR_ERROR,
    },
    "never_scanned": {
        "text": "Never scanned",
        "color": tokens.TEXT_MUTED,
    },
    "path_missing": {
        "text": "Path missing",
        "color": tokens.COLOR_ERROR,
    },
    "success": {
        "text": "Success",
        "color": tokens.COLOR_SUCCESS,
    },
    "neutral": {
        "text": "Neutral",
        "color": tokens.TEXT_MUTED,
    },
}


def pill_font() -> QFont:
    font = QFont()
    font.setPointSize(tokens.FONT_SIZE_SM)  # 12px
    font.setWeight(QFont.Weight.Medium)
    return font


def draw_pill(painter: QPainter, rect: QRectF, text: str, color_hex: str, align_center: bool = True) -> QRectF:
    """Draw a painter-rendered rounded rectangle pill (radius = height/2) with horizontal padding."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    font = pill_font()
    painter.setFont(font)

    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    text_width = fm.horizontalAdvance(text)
    pill_w = text_width + 20  # 10px horizontal padding on left and right
    pill_h = 22

    if align_center and rect.width() > pill_w:
        x = rect.x() + (rect.width() - pill_w) / 2
    else:
        x = rect.x()
    y = rect.y() + (rect.height() - pill_h) / 2
    pill_rect = QRectF(x, y, pill_w, pill_h)

    # Tinted fill at low alpha
    fill_col = QColor(color_hex)
    fill_col.setAlpha(36)

    border_col = QColor(color_hex)
    border_col.setAlpha(130)

    painter.setPen(QPen(border_col, 1))
    painter.setBrush(fill_col)

    # Pill radius = height / 2
    r = pill_h / 2.0
    painter.drawRoundedRect(pill_rect, r, r)

    painter.setPen(QColor(color_hex))
    painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, text)

    painter.restore()
    return pill_rect


class StatusPillDelegate(QStyledItemDelegate):
    """Painter-based item delegate rendering rounded status pills in tables."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        status_key = str(index.data(Qt.ItemDataRole.DisplayRole) or "ok").lower()
        config = STATUS_CONFIG.get(status_key, STATUS_CONFIG["ok"])

        painter.save()

        # Draw selection/hover row background
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(tokens.ACCENT_BG))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(tokens.SURFACE_2))

        cell_rect = QRectF(option.rect)
        draw_pill(painter, cell_rect, config["text"], config["color"], align_center=True)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        status_key = str(index.data(Qt.ItemDataRole.DisplayRole) or "ok").lower()
        config = STATUS_CONFIG.get(status_key, STATUS_CONFIG["ok"])
        fm = option.fontMetrics
        w = fm.horizontalAdvance(config["text"]) + 24
        return QSize(max(w, 80), 32)


class StatusPillWidget(QWidget):
    """Standalone status pill widget for cards, profile rows, and detail drawer."""

    def __init__(self, status: str = "ok", parent: QWidget | None = None, label: str | None = None) -> None:
        super().__init__(parent)
        self._status = status.lower()
        self._custom_label = label
        self.setFixedHeight(26)
        self._update_geometry()

    def _update_geometry(self) -> None:
        config = STATUS_CONFIG.get(self._status, STATUS_CONFIG["ok"])
        text = self._custom_label if self._custom_label is not None else config["text"]
        font = pill_font()
        self.setFont(font)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 24
        self.setFixedWidth(max(w, 56))

    def set_status(self, status: str, label: str | None = None) -> None:
        self._status = status.lower()
        if label is not None:
            self._custom_label = label
        self._update_geometry()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        config = STATUS_CONFIG.get(self._status, STATUS_CONFIG["ok"])
        text = self._custom_label if self._custom_label is not None else config["text"]
        rect = QRectF(0, 0, self.width(), self.height())
        draw_pill(painter, rect, text, config["color"], align_center=True)
