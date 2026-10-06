"""Application navigation sidebar supporting collapsed icon-only mode."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mmis.ui import strings
from mmis.ui.theme import icons, tokens


class NavButton(QPushButton):
    """Navigation button supporting active indicator and icon-only collapse."""

    def __init__(self, page_id: str, label_text: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_id = page_id
        self._label_text = label_text
        self._icon_name = icon_name
        self._collapsed = False

        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.update_style(False)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        if collapsed:
            self.setText("")
            self.setToolTip(self._label_text)
            self.setIconSize(QSize(20, 20))
        else:
            self.setText(f"  {self._label_text}")
            self.setToolTip("")
            self.setIconSize(QSize(18, 18))
        self.update_style(self.isChecked())

    def update_style(self, checked: bool) -> None:
        color = tokens.ACCENT if checked else tokens.TEXT_PRIMARY
        icon = icons.get_tinted_icon(self._icon_name, color_hex=color, size=20)
        self.setIcon(icon)

        bg = tokens.ACCENT_BG if checked else "transparent"
        border_left = f"3px solid {tokens.ACCENT}" if checked and not self._collapsed else "3px solid transparent"
        font_weight = tokens.FONT_WEIGHT_SEMIBOLD if checked else tokens.FONT_WEIGHT_NORMAL

        align = "center" if self._collapsed else "left"
        padding_left = f"{tokens.SPACE_XS}px" if self._collapsed else f"{tokens.SPACE_MD}px"

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: none;
                border-left: {border_left};
                border-radius: {tokens.RADIUS_MD}px;
                text-align: {align};
                padding-left: {padding_left};
                font-weight: {font_weight};
                font-size: {tokens.FONT_SIZE_BASE}px;
            }}
            QPushButton:hover {{
                background-color: {tokens.SURFACE_2};
            }}
        """)


class Sidebar(QFrame):
    """Left navigation panel with brand banner and collapsible page links."""

    page_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setProperty("card", "true")
        self._collapsed = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD)
        self._layout.setSpacing(tokens.SPACE_XS)

        # Brand header
        self._brand_layout = QHBoxLayout()
        self._brand_layout.setContentsMargins(tokens.SPACE_XS, 0, tokens.SPACE_XS, tokens.SPACE_MD)
        self._brand_layout.setSpacing(tokens.SPACE_SM)

        self._brand_icon = QLabel()
        self._brand_icon.setPixmap(icons.get_tinted_pixmap("package", color_hex=tokens.ACCENT, size=26))
        self._brand_layout.addWidget(self._brand_icon)

        self._brand_title = QLabel(strings.APP_SHORT_TITLE)
        self._brand_title.setProperty("heading", "true")
        brand_font = self._brand_title.font()
        brand_font.setPointSize(tokens.FONT_SIZE_LG)
        brand_font.setWeight(QFont.Weight.Bold)
        self._brand_title.setFont(brand_font)
        self._brand_layout.addWidget(self._brand_title)

        self._brand_layout.addStretch()
        self._layout.addLayout(self._brand_layout)

        # Nav button group
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        self._buttons: dict[str, NavButton] = {}

        nav_items = [
            ("dashboard", strings.NAV_DASHBOARD, "dashboard"),
            ("profiles", strings.NAV_PROFILES, "profiles"),
            ("mods", strings.NAV_MODS, "mods"),
            ("issues", strings.NAV_ISSUES, "alert_triangle"),
            ("compare", strings.NAV_COMPARE, "compare"),
            ("crashes", strings.NAV_CRASHES, "crash"),
            ("reports", strings.NAV_REPORTS, "report"),
            ("backup", strings.NAV_BACKUP, "backup"),
        ]

        for page_id, label, icon in nav_items:
            btn = NavButton(page_id, label, icon, self)
            btn.toggled.connect(lambda checked, b=btn: self._on_btn_toggled(b, checked))
            self._button_group.addButton(btn)
            self._buttons[page_id] = btn
            self._layout.addWidget(btn)

        self._layout.addStretch()

        # Version label at bottom
        self._ver_label = QLabel("v0.1.0")
        self._ver_label.setProperty("caption", "true")
        self._ver_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._ver_label)

        self.set_collapsed(False)
        self.select_page("dashboard")

    def _on_btn_toggled(self, button: NavButton, checked: bool) -> None:
        button.update_style(checked)
        if checked:
            self.page_selected.emit(button.page_id)

    def select_page(self, page_id: str) -> None:
        btn = self._buttons.get(page_id)
        if btn and not btn.isChecked():
            btn.setChecked(True)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        if collapsed:
            self.setFixedWidth(68)
            self._brand_title.hide()
            self._ver_label.hide()
            self._brand_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.setFixedWidth(220)
            self._brand_title.show()
            self._ver_label.show()
            self._brand_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        for btn in self._buttons.values():
            btn.set_collapsed(collapsed)
