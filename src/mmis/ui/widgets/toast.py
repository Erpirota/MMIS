"""Toast notification overlay with auto-dismiss and error codes."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class ToastItem(QFrame):
    """Individual floating toast alert card."""

    def __init__(
        self,
        title: str = "",
        message: str = "",
        kind: str = "info",  # "success", "error", "warning", "info"
        code: str | None = None,
        duration_ms: int = 3500,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card_elevated", "true")
        self.setMaximumWidth(360)
        self.setMinimumWidth(260)
        self.setStyleSheet(
            f"ToastItem {{ background-color: {tokens.SURFACE_3}; "
            f"border: 1px solid {tokens.BORDER_HAIRLINE}; "
            f"border-radius: {tokens.RADIUS_MD}px; }}"
        )

        display_msg = message if message else title

        accent_color = {
            "success": tokens.COLOR_SUCCESS,
            "error": tokens.COLOR_ERROR,
            "warning": tokens.COLOR_WARNING,
            "info": tokens.COLOR_INFO,
        }.get(kind, tokens.COLOR_INFO)

        icon_name = {
            "success": "check",
            "error": "alert_circle",
            "warning": "alert_triangle",
            "info": "info",
        }.get(kind, "info")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_SM, tokens.SPACE_SM)
        layout.setSpacing(tokens.SPACE_SM)

        # Status icon
        icon_label = QLabel()
        icon_label.setPixmap(icons.get_tinted_pixmap(icon_name, color_hex=accent_color, size=18))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Content column
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(tokens.SPACE_XXS)

        msg_label = QLabel(display_msg)
        msg_label.setProperty("subheading", "true")
        msg_label.setStyleSheet(f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.FONT_SIZE_BASE}px;")
        msg_label.setWordWrap(True)
        content_layout.addWidget(msg_label)

        if code:
            code_label = QLabel(f"Code: {code}")
            code_label.setProperty("code", "true")
            code_label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
            content_layout.addWidget(code_label)

        layout.addLayout(content_layout, 1)

        # Close button
        close_btn = QPushButton()
        close_btn.setProperty("ghost", "true")
        close_btn.setIcon(icons.get_tinted_icon("close", color_hex=tokens.TEXT_MUTED, size=14))
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.dismiss)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Auto-dismiss timer
        if duration_ms > 0:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(duration_ms)
            self._timer.timeout.connect(self.dismiss)
            self._timer.start()

    def dismiss(self) -> None:
        overlay = self.parentWidget()
        self.hide()
        self.deleteLater()
        if isinstance(overlay, ToastOverlay):
            overlay.reposition()


class ToastOverlay(QWidget):
    """Floating container managing active toast notifications in window bottom-right."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, tokens.SPACE_MD, tokens.SPACE_MD)
        self._layout.setSpacing(tokens.SPACE_SM)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        # The overlay sits above every page: it must never cover more than its toasts,
        # or it swallows the mouse clicks meant for the widgets underneath.
        self.hide()

    def has_toasts(self) -> bool:
        return any(
            self._layout.itemAt(i).widget() is not None and not self._layout.itemAt(i).widget().isHidden()
            for i in range(self._layout.count())
        )

    def reposition(self) -> None:
        """Cover only the bottom-right area taken by the active toasts; hide when there are none."""
        parent = self.parentWidget()
        if parent is None:
            return
        if not self.has_toasts():
            self.hide()
            return
        self._layout.activate()
        hint = self._layout.sizeHint()
        width = min(hint.width(), parent.width())
        height = min(hint.height(), parent.height())
        self.setGeometry(parent.width() - width, parent.height() - height, width, height)
        self.show()
        self.raise_()

    def show_toast(
        self,
        title: str,
        message: str,
        kind: str = "info",
        code: str | None = None,
        duration_ms: int = 3500,
    ) -> ToastItem:
        toast = ToastItem(
            title=title,
            message=message,
            kind=kind,
            code=code,
            duration_ms=duration_ms,
            parent=self,
        )
        self._layout.addWidget(toast)
        toast.show()
        self.reposition()
        return toast
