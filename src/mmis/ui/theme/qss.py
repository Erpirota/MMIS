"""Stylesheet (QSS) builder using design system tokens.

Ensures that no visual parameters are hard-coded in views or widgets.
"""

from __future__ import annotations

from mmis.ui.theme import tokens


def build_stylesheet() -> str:
    """Generate comprehensive dark-mode QSS completely derived from design tokens."""
    return f"""
/* --- Global Window & Base Widgets --- */
QMainWindow, QDialog {{
    background-color: {tokens.BG_WINDOW};
    color: {tokens.TEXT_PRIMARY};
    font-family: {tokens.FONT_FAMILY};
    font-size: {tokens.FONT_SIZE_BASE}px;
}}

QWidget {{
    color: {tokens.TEXT_PRIMARY};
    font-family: {tokens.FONT_FAMILY};
    font-size: {tokens.FONT_SIZE_BASE}px;
}}

QWidget:focus {{
    outline: none;
}}

/* --- Cards and Containers --- */
QFrame[card="true"] {{
    background-color: {tokens.SURFACE_1};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_LG}px;
}}

QFrame[card_elevated="true"] {{
    background-color: {tokens.SURFACE_2};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_LG}px;
}}

/* --- Typography Helpers --- */
QLabel[heading="true"] {{
    font-size: {tokens.FONT_SIZE_XL}px;
    font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};
    color: {tokens.TEXT_PRIMARY};
}}

QLabel[subheading="true"] {{
    font-size: {tokens.FONT_SIZE_MD}px;
    color: {tokens.TEXT_SECONDARY};
}}

QLabel[section_header="true"] {{
    font-size: {tokens.FONT_SIZE_SM}px;
    font-weight: {tokens.FONT_WEIGHT_BOLD};
    color: {tokens.TEXT_SECONDARY};
}}

QLabel[caption="true"] {{
    font-size: {tokens.FONT_SIZE_XS}px;
    color: {tokens.TEXT_MUTED};
}}

QLabel[code="true"] {{
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: {tokens.FONT_SIZE_SM}px;
    color: {tokens.TEXT_MUTED};
}}

/* --- Buttons --- */
QPushButton {{
    background-color: {tokens.SURFACE_2};
    color: {tokens.TEXT_PRIMARY};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_MD}px;
    padding: 6px 14px;
    font-size: {tokens.FONT_SIZE_BASE}px;
    font-weight: {tokens.FONT_WEIGHT_MEDIUM};
    min-height: 22px;
}}

QPushButton:hover {{
    background-color: {tokens.SURFACE_3};
    border-color: {tokens.TEXT_MUTED};
}}

QPushButton:pressed {{
    background-color: {tokens.SURFACE_1};
}}

QPushButton:focus {{
    border: 1px solid {tokens.BORDER_FOCUS};
}}

QPushButton:disabled {{
    background-color: {tokens.COLOR_DISABLED_BG};
    color: {tokens.COLOR_DISABLED};
    border-color: {tokens.BORDER_HAIRLINE};
}}

QPushButton[primary="true"] {{
    background-color: {tokens.ACCENT};
    color: {tokens.TEXT_ON_ACCENT};
    border: 1px solid {tokens.ACCENT};
    font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};
}}

QPushButton[primary="true"]:hover {{
    background-color: {tokens.ACCENT_HOVER};
    border-color: {tokens.ACCENT_HOVER};
}}

QPushButton[primary="true"]:pressed {{
    background-color: {tokens.ACCENT_PRESSED};
    border-color: {tokens.ACCENT_PRESSED};
}}

QPushButton[primary="true"]:disabled {{
    background-color: {tokens.COLOR_DISABLED_BG};
    color: {tokens.COLOR_DISABLED};
    border-color: {tokens.BORDER_HAIRLINE};
}}

QPushButton[destructive="true"] {{
    background-color: {tokens.COLOR_ERROR_BG};
    color: {tokens.COLOR_ERROR};
    border: 1px solid {tokens.COLOR_ERROR};
}}

QPushButton[destructive="true"]:hover {{
    background-color: {tokens.COLOR_ERROR};
    color: {tokens.TEXT_PRIMARY};
}}

QPushButton[destructive="true"]:pressed {{
    background-color: {tokens.SURFACE_1};
}}

QPushButton[ghost="true"] {{
    background-color: transparent;
    border: none;
    padding: {tokens.SPACE_XXS}px {tokens.SPACE_XS}px;
}}

QPushButton[ghost="true"]:hover {{
    background-color: {tokens.SURFACE_2};
}}

/* --- Input Fields --- */
QLineEdit {{
    background-color: {tokens.SURFACE_2};
    color: {tokens.TEXT_PRIMARY};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_MD}px;
    padding: {tokens.SPACE_XS}px {tokens.SPACE_SM}px;
    font-size: {tokens.FONT_SIZE_BASE}px;
}}

QLineEdit:focus {{
    border: 1px solid {tokens.BORDER_FOCUS};
    background-color: {tokens.SURFACE_2};
}}

QLineEdit:disabled {{
    background-color: {tokens.COLOR_DISABLED_BG};
    color: {tokens.COLOR_DISABLED};
}}

/* --- Combo Boxes --- */
QComboBox {{
    background-color: {tokens.SURFACE_2};
    color: {tokens.TEXT_PRIMARY};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_MD}px;
    padding: {tokens.SPACE_XS}px {tokens.SPACE_SM}px;
    min-height: 22px;
}}

QComboBox:focus {{
    border: 1px solid {tokens.BORDER_FOCUS};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: none;
}}

QComboBox QAbstractItemView {{
    background-color: {tokens.SURFACE_3};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_MD}px;
    color: {tokens.TEXT_PRIMARY};
    selection-background-color: {tokens.ACCENT_BG};
    selection-color: {tokens.TEXT_PRIMARY};
    padding: {tokens.SPACE_XXS}px;
}}

/* --- Table View & Headers --- */
QTableView {{
    background-color: {tokens.SURFACE_1};
    alternate-background-color: {tokens.SURFACE_1};
    color: {tokens.TEXT_PRIMARY};
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_LG}px;
    gridline-color: transparent;
    selection-background-color: {tokens.ACCENT_BG};
    selection-color: {tokens.TEXT_PRIMARY};
    outline: none;
}}

QTableView::item {{
    padding: {tokens.SPACE_XS}px {tokens.SPACE_SM}px;
    border-bottom: 1px solid {tokens.BORDER_HAIRLINE};
}}

QTableView::item:selected {{
    background-color: {tokens.ACCENT_BG};
    color: {tokens.TEXT_PRIMARY};
}}

QTableView::item:hover {{
    background-color: {tokens.SURFACE_2};
}}

QHeaderView::section {{
    background-color: {tokens.SURFACE_2};
    color: {tokens.TEXT_SECONDARY};
    padding: {tokens.SPACE_SM}px 24px {tokens.SPACE_SM}px {tokens.SPACE_SM}px;
    border: none;
    border-bottom: 1px solid {tokens.BORDER_HAIRLINE};
    font-size: {tokens.FONT_SIZE_SM}px;
    font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};
}}

QHeaderView::down-arrow {{
    width: 10px;
    height: 10px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    margin-right: 8px;
}}

QHeaderView::up-arrow {{
    width: 10px;
    height: 10px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
    margin-right: 8px;
}}

/* --- Scroll Area & Viewports --- */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QWidget#qt_scrollarea_viewport {{
    background-color: transparent;
}}

/* --- Themed Custom Scrollbars (No Windows default hatched chrome) --- */
QScrollBar:vertical {{
    background: transparent;
    width: 6px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {tokens.BORDER_HAIRLINE};
    min-height: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical:hover {{
    background: {tokens.TEXT_MUTED};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
    border: none;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 6px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: {tokens.BORDER_HAIRLINE};
    min-width: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {tokens.TEXT_MUTED};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
    border: none;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* --- Check Boxes --- */
QCheckBox {{
    color: {tokens.TEXT_PRIMARY};
    spacing: {tokens.SPACE_XS}px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {tokens.BORDER_HAIRLINE};
    border-radius: {tokens.RADIUS_SM}px;
    background-color: {tokens.SURFACE_2};
}}

QCheckBox::indicator:hover {{
    border-color: {tokens.TEXT_MUTED};
}}

QCheckBox::indicator:checked {{
    background-color: {tokens.ACCENT};
    border-color: {tokens.ACCENT};
}}

/* --- Splitter --- */
QSplitter::handle {{
    background-color: {tokens.BORDER_HAIRLINE};
}}

QSplitter::handle:hover {{
    background-color: {tokens.ACCENT};
}}
"""
