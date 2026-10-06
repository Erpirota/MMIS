"""Unified container for rendering Empty, Loading, and Error states."""

from __future__ import annotations

from enum import StrEnum
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QTransform
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class PageState(StrEnum):
    EMPTY = "empty"
    LOADING = "loading"
    ERROR = "error"
    READY = "ready"


class StateCard(QFrame):
    """Card widget providing clear visuals and recovery actions for empty, loading, or error states."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setObjectName("stateCard")

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL)
        self._layout.setSpacing(tokens.SPACE_MD)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon / Spinner label
        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._icon_label)

        # Title
        self._title_label = QLabel()
        self._title_label.setProperty("heading", "true")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = self._title_label.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)
        self._layout.addWidget(self._title_label)

        # Description
        self._desc_label = QLabel()
        self._desc_label.setProperty("subheading", "true")
        self._desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_label.setWordWrap(True)
        self._layout.addWidget(self._desc_label)

        # Technical Error Code (secondary monospace line)
        self._code_label = QLabel()
        self._code_label.setProperty("code", "true")
        self._code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code_label.setVisible(False)
        self._layout.addWidget(self._code_label)

        # Primary Action Button
        self._action_btn = QPushButton()
        self._action_btn.setProperty("primary", "true")
        self._action_btn.setVisible(False)
        self._layout.addWidget(self._action_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self._action_callback: Callable[[], None] | None = None
        self._action_btn.clicked.connect(self._handle_action_clicked)

        # Spinner animation timer
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(60)
        self._spinner_timer.timeout.connect(self._rotate_spinner)

    def _rotate_spinner(self) -> None:
        self._spinner_angle = (self._spinner_angle + 30) % 360
        base_pixmap = icons.get_tinted_pixmap("spinner", color_hex=tokens.ACCENT, size=40)
        transform = QTransform().rotate(self._spinner_angle)
        rotated = base_pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        self._icon_label.setPixmap(rotated)

    def _handle_action_clicked(self) -> None:
        if self._action_callback is not None:
            self._action_callback()

    def show_empty(
        self,
        title: str = strings.STATE_EMPTY_TITLE,
        description: str = "",
        action_text: str | None = None,
        on_action: Callable[[], None] | None = None,
        icon_name: str = "package",
    ) -> None:
        self._spinner_timer.stop()
        self._icon_label.setPixmap(icons.get_tinted_pixmap(icon_name, color_hex=tokens.TEXT_SECONDARY, size=48))
        self._title_label.setText(title)
        self._desc_label.setText(description)
        self._code_label.setVisible(False)

        if action_text and on_action:
            self._action_btn.setText(action_text)
            self._action_callback = on_action
            self._action_btn.setVisible(True)
        else:
            self._action_btn.setVisible(False)

        self.setVisible(True)

    def show_loading(
        self,
        title: str = strings.STATE_LOADING_TITLE,
        description: str = strings.STATE_LOADING_DESC,
    ) -> None:
        self._icon_label.setPixmap(icons.get_tinted_pixmap("spinner", color_hex=tokens.ACCENT, size=40))
        self._title_label.setText(title)
        self._desc_label.setText(description)
        self._code_label.setVisible(False)
        self._action_btn.setVisible(False)
        self._spinner_timer.start()
        self.setVisible(True)

    def show_error(
        self,
        title: str = strings.STATE_ERROR_TITLE,
        description: str = "",
        code: str | None = None,
        action_text: str | None = None,
        on_action: Callable[[], None] | None = None,
    ) -> None:
        self._spinner_timer.stop()
        self._icon_label.setPixmap(icons.get_tinted_pixmap("alert_triangle", color_hex=tokens.COLOR_ERROR, size=48))
        self._title_label.setText(title)
        self._desc_label.setText(description)

        if code:
            self._code_label.setText(f"Code: {code}")
            self._code_label.setVisible(True)
        else:
            self._code_label.setVisible(False)

        if action_text and on_action:
            self._action_btn.setText(action_text)
            self._action_callback = on_action
            self._action_btn.setVisible(True)
        else:
            self._action_btn.setVisible(False)

        self.setVisible(True)
