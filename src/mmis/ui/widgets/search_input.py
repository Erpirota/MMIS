"""Custom search input field with integrated icon and clear action."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLineEdit, QWidget

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class SearchInput(QLineEdit):
    """Search input field with embedded search icon and clear button."""

    search_changed = Signal(str)

    def __init__(self, placeholder: str = strings.MODS_SEARCH_PLACEHOLDER, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)

        # Leading search icon action
        search_icon = icons.get_tinted_icon("search", color_hex=tokens.TEXT_MUTED, size=16)
        self.addAction(search_icon, QLineEdit.ActionPosition.LeadingPosition)

        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        self.search_changed.emit(text.strip())
