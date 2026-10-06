"""Modal confirmation dialog styled with design system tokens."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class ConfirmDialog(QDialog):
    """Destructive action confirmation dialog with explicit risk warnings."""

    def __init__(
        self,
        title: str,
        message: str,
        warning_note: str,
        confirm_text: str = strings.DIALOG_DELETE_CONFIRM_BTN,
        cancel_text: str = strings.DIALOG_CANCEL_BTN,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        layout.setSpacing(tokens.SPACE_MD)

        # Header row: Alert icon + Message
        header_layout = QHBoxLayout()
        header_layout.setSpacing(tokens.SPACE_MD)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        icon_label = QLabel()
        icon_label.setPixmap(icons.get_tinted_pixmap("alert_triangle", color_hex=tokens.COLOR_ERROR, size=32))
        header_layout.addWidget(icon_label)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(tokens.SPACE_XS)

        self._msg_label = QLabel(message)
        self._msg_label.setProperty("heading", "true")
        self._msg_label.setWordWrap(True)
        msg_layout.addWidget(self._msg_label)

        self._warning_label = QLabel(warning_note)
        self._warning_label.setProperty("subheading", "true")
        self._warning_label.setWordWrap(True)
        msg_layout.addWidget(self._warning_label)

        header_layout.addLayout(msg_layout)
        layout.addLayout(header_layout)

        # Button row: Cancel is default; Confirm is destructive and NOT default
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(tokens.SPACE_MD)
        btn_layout.addStretch()

        self._cancel_btn = QPushButton(cancel_text)
        self._cancel_btn.setDefault(True)
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._cancel_btn)

        self._confirm_btn = QPushButton(confirm_text)
        self._confirm_btn.setProperty("destructive", "true")
        self._confirm_btn.setDefault(False)
        self._confirm_btn.setAutoDefault(False)
        self._confirm_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._confirm_btn)

        layout.addLayout(btn_layout)
