"""Stat card display widget showing key numerical metrics and breakdowns."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mmis.ui.theme import icons, tokens


class StatCard(QFrame):
    """Card widget displaying a metric title, prominent value, and breakdown description."""

    def __init__(
        self,
        title: str,
        value: str,
        subtitle: str = "",
        icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("card", "true")
        self.setMinimumHeight(100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        layout.setSpacing(tokens.SPACE_XS)

        # Header row: title + optional icon
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        self._title_label = QLabel(title.upper())
        self._title_label.setProperty("section_header", "true")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        if icon_name:
            icon_label = QLabel()
            icon_label.setPixmap(icons.get_tinted_pixmap(icon_name, color_hex=tokens.TEXT_SECONDARY, size=16))
            header_layout.addWidget(icon_label)

        layout.addLayout(header_layout)

        # Large value
        self._val_label = QLabel(value)
        self._val_label.setProperty("heading", "true")
        val_font = self._val_label.font()
        val_font.setPointSize(tokens.FONT_SIZE_XXL)
        val_font.setWeight(QFont.Weight.Bold)
        self._val_label.setFont(val_font)
        layout.addWidget(self._val_label)

        # Subtitle / description
        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setProperty("subheading", "true")
        self._subtitle_label.setWordWrap(True)
        layout.addWidget(self._subtitle_label)

        layout.addStretch()

    def update_metrics(self, value: str, subtitle: str = "") -> None:
        self._val_label.setText(value)
        self._subtitle_label.setText(subtitle)
