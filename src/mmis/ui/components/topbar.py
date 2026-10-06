"""Application top navigation bar containing profile selector and scan action."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class ScanButton(QPushButton):
    """Primary scan button with an exact 8px gap between icon and label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("primary", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._inner_layout = QHBoxLayout(self)
        self._inner_layout.setContentsMargins(14, 6, 14, 6)
        self._inner_layout.setSpacing(8)
        self._inner_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._inner_layout.addWidget(self._icon_lbl)

        self._text_lbl = QLabel(self)
        self._text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text_lbl.setStyleSheet(
            f"color: {tokens.TEXT_ON_ACCENT}; font-weight: {tokens.FONT_WEIGHT_SEMIBOLD}; font-size: {tokens.FONT_SIZE_BASE}px; background: transparent;"
        )
        self._inner_layout.addWidget(self._text_lbl)

    def sizeHint(self) -> QSize:
        fm = self._text_lbl.fontMetrics()
        text_str = self._text_lbl.text() or strings.TOPBAR_SCAN_BTN
        text_w = fm.horizontalAdvance(text_str)
        # width >= text width + icon (16) + gap (8) + horizontal padding (28)
        w = text_w + 16 + 8 + 28
        h = max(32, fm.height() + 12)
        return QSize(max(w, 110), h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def set_content(self, text: str, icon_name: str) -> None:
        self._text_lbl.setText(text)
        self._icon_lbl.setPixmap(icons.get_tinted_pixmap(icon_name, color_hex=tokens.TEXT_ON_ACCENT, size=16))
        self.updateGeometry()

    def text(self) -> str:
        return self._text_lbl.text()

    def setText(self, text: str) -> None:
        self._text_lbl.setText(text)
        self.updateGeometry()


class TopBar(QFrame):
    """Header bar with profile switcher and primary scan action button."""

    profile_changed = Signal(str)
    scan_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setProperty("card", "true")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS)
        layout.setSpacing(tokens.SPACE_MD)

        # Profile selection label
        self._profile_label = QLabel(strings.TOPBAR_PROFILE_LABEL)
        self._profile_label.setProperty("subheading", "true")
        layout.addWidget(self._profile_label)

        # Profile combo box
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(220)
        self._profile_combo.currentTextChanged.connect(self._on_profile_selected)
        layout.addWidget(self._profile_combo)

        layout.addStretch()

        # Primary Scan Action button with exact 8px gap between icon and label
        self._scan_btn = ScanButton(self)
        self._scan_btn.set_content(strings.TOPBAR_SCAN_BTN, "scan")
        self._scan_btn.setToolTip(strings.TOPBAR_SCAN_TOOLTIP)
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self._scan_btn)

    def _on_profile_selected(self, name: str) -> None:
        if name and name != strings.TOPBAR_NO_PROFILE:
            self.profile_changed.emit(name)

    def _on_scan_clicked(self) -> None:
        current = self.current_profile()
        if current:
            self.scan_requested.emit(current)

    def current_profile(self) -> str | None:
        current = self._profile_combo.currentText().strip()
        if not current or current == strings.TOPBAR_NO_PROFILE:
            return None
        return current

    def set_current_profile(self, name: str) -> None:
        if self.current_profile() != name:
            self._profile_combo.blockSignals(True)
            self._profile_combo.setCurrentText(name)
            self._profile_combo.blockSignals(False)

    def set_profiles(self, profiles: list[str], active_profile: str | None = None) -> None:
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()

        if not profiles:
            self._profile_combo.addItem(strings.TOPBAR_NO_PROFILE)
            self._profile_combo.setEnabled(False)
            self._scan_btn.setEnabled(False)
        else:
            self._profile_combo.setEnabled(True)
            self._scan_btn.setEnabled(True)
            for name in profiles:
                self._profile_combo.addItem(name)

            if active_profile and active_profile in profiles:
                self._profile_combo.setCurrentText(active_profile)
            elif profiles:
                self._profile_combo.setCurrentIndex(0)

        self._profile_combo.blockSignals(False)

    def set_scanning(self, is_scanning: bool) -> None:
        if is_scanning:
            self._scan_btn.setEnabled(False)
            self._scan_btn.set_content(strings.TOPBAR_SCANNING_BTN, "spinner")
            self._profile_combo.setEnabled(False)
        else:
            self._scan_btn.setEnabled(self._profile_combo.count() > 0 and self.current_profile() is not None)
            self._scan_btn.set_content(strings.TOPBAR_SCAN_BTN, "scan")
            self._profile_combo.setEnabled(True)

