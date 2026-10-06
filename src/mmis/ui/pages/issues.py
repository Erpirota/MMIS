"""Issues page displaying diagnostic findings with filtering, grouping, and mod navigation."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mmis.domain.profiles import ScanRecord
from mmis.services.issues_view import (
    IssueEvidence,
    IssueFilterCriteria,
    IssueItem,
    build_issues_view,
    filter_issues,
    group_issues,
)
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.elided_label import ElidedLabel
from mmis.ui.widgets.search_input import SearchInput
from mmis.ui.widgets.state_card import PageState, StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget


class FilterChip(QPushButton):
    """Toggleable filter chip displaying a category/metric name and count."""

    def __init__(self, key: str, label: str, count: int = 0, color_role: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._base_label = label
        self._count = count
        self.color_role = color_role
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_text()
        self.toggled.connect(self._on_toggled)
        self._update_style()

    def set_count(self, count: int) -> None:
        self._count = count
        self.update_text()

    def update_text(self) -> None:
        self.setText(f"{self._base_label} ({self._count})")

    def _on_toggled(self, checked: bool) -> None:
        self._update_style()

    def _update_style(self) -> None:
        checked = self.isChecked()
        if checked:
            if self.color_role == "error":
                bg = tokens.COLOR_ERROR_BG
                color = tokens.COLOR_ERROR
                border = tokens.COLOR_ERROR
            elif self.color_role == "warning":
                bg = tokens.COLOR_WARNING_BG
                color = tokens.COLOR_WARNING
                border = tokens.COLOR_WARNING
            elif self.color_role == "info":
                bg = tokens.COLOR_INFO_BG
                color = tokens.COLOR_INFO
                border = tokens.COLOR_INFO
            elif self.color_role == "neutral":
                bg = tokens.SURFACE_3
                color = tokens.TEXT_PRIMARY
                border = tokens.TEXT_SECONDARY
            else:  # accent
                bg = tokens.ACCENT_BG
                color = tokens.ACCENT
                border = tokens.ACCENT
        else:
            # Unselected: neutral muted outline
            bg = tokens.SURFACE_1
            color = tokens.TEXT_MUTED
            border = tokens.BORDER_HAIRLINE

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: {tokens.RADIUS_MD}px;
                padding: 4px 10px;
                font-size: {tokens.FONT_SIZE_SM}px;
                font-weight: {tokens.FONT_WEIGHT_MEDIUM};
            }}
            QPushButton:hover {{
                background-color: {tokens.SURFACE_3};
            }}
        """)


class CertaintyBadge(QFrame):
    """Visual treatment for Certainty class using distinct icon and neutral border style."""

    def __init__(self, certainty: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("certaintyBadge")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 8, 2)
        layout.setSpacing(4)

        icon_lbl = QLabel(self)
        text_lbl = QLabel(certainty, self)

        c_lower = certainty.lower()
        if "certain" in c_lower:
            # Solid neutral border with check icon
            icon_lbl.setPixmap(icons.get_tinted_pixmap("check", color_hex=tokens.TEXT_SECONDARY, size=12))
            self.setStyleSheet(f"""
                QFrame#certaintyBadge {{
                    background-color: {tokens.SURFACE_2};
                    border: 1px solid {tokens.TEXT_SECONDARY};
                    border-radius: {tokens.RADIUS_SM}px;
                }}
                QFrame#certaintyBadge QLabel {{
                    border: none;
                    background: transparent;
                    color: {tokens.TEXT_PRIMARY};
                    font-size: {tokens.FONT_SIZE_XS}px;
                    font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};
                }}
            """)
        elif "possible" in c_lower:
            # Dashed neutral border with alert-circle icon
            icon_lbl.setPixmap(icons.get_tinted_pixmap("alert_circle", color_hex=tokens.TEXT_MUTED, size=12))
            self.setStyleSheet(f"""
                QFrame#certaintyBadge {{
                    background-color: {tokens.SURFACE_1};
                    border: 1px dashed {tokens.BORDER_HAIRLINE};
                    border-radius: {tokens.RADIUS_SM}px;
                }}
                QFrame#certaintyBadge QLabel {{
                    border: none;
                    background: transparent;
                    color: {tokens.TEXT_SECONDARY};
                    font-size: {tokens.FONT_SIZE_XS}px;
                    font-weight: {tokens.FONT_WEIGHT_MEDIUM};
                }}
            """)
        else:
            # Dotted neutral border with info icon
            icon_lbl.setPixmap(icons.get_tinted_pixmap("info", color_hex=tokens.TEXT_MUTED, size=12))
            self.setStyleSheet(f"""
                QFrame#certaintyBadge {{
                    background-color: transparent;
                    border: 1px dotted {tokens.BORDER_HAIRLINE};
                    border-radius: {tokens.RADIUS_SM}px;
                }}
                QFrame#certaintyBadge QLabel {{
                    border: none;
                    background: transparent;
                    color: {tokens.TEXT_MUTED};
                    font-size: {tokens.FONT_SIZE_XS}px;
                    font-weight: {tokens.FONT_WEIGHT_MEDIUM};
                }}
            """)

        layout.addWidget(icon_lbl)
        layout.addWidget(text_lbl)


class IssueCard(QFrame):
    """Expandable issue card displaying finding details, evidence table, and mod navigation."""

    show_in_mods_clicked = Signal(str)

    def __init__(self, item: IssueItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self._is_expanded = False
        self.setObjectName("issueCard")
        self.setProperty("card", "true")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(tokens.SPACE_MD, 8, tokens.SPACE_MD, 8)
        self._main_layout.setSpacing(tokens.SPACE_XS)

        # Header Row: Severity Pill | Certainty Badge | Code | Message | Expand Toggle
        self._header_widget = QWidget(self)
        self._header_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_widget.mousePressEvent = lambda e: self.toggle_expanded()
        self._header_widget.setFixedHeight(48)
        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(tokens.SPACE_SM)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._sev_pill = StatusPillWidget(item.severity, self._header_widget)
        header_layout.addWidget(self._sev_pill)

        self._cert_badge = CertaintyBadge(item.certainty, self._header_widget)
        header_layout.addWidget(self._cert_badge)

        self._code_label = QLabel(item.code, self._header_widget)
        self._code_label.setProperty("code", "true")
        header_layout.addWidget(self._code_label)

        self._msg_label = ElidedLabel(item.message, self._header_widget)
        self._msg_label.setProperty("subheading", "true")
        header_layout.addWidget(self._msg_label, 1)

        self._toggle_btn = QPushButton(self._header_widget)
        self._toggle_btn.setProperty("ghost", "true")
        self._toggle_btn.setIcon(icons.get_tinted_icon("chevron_down", color_hex=tokens.TEXT_SECONDARY, size=16))
        self._toggle_btn.clicked.connect(self.toggle_expanded)
        header_layout.addWidget(self._toggle_btn)

        self._main_layout.addWidget(self._header_widget)

        # Expandable Detail Container
        self._detail_container = QWidget(self)
        self._detail_layout = QVBoxLayout(self._detail_container)
        self._detail_layout.setContentsMargins(tokens.SPACE_XS, tokens.SPACE_XS, tokens.SPACE_XS, tokens.SPACE_XS)
        self._detail_layout.setSpacing(tokens.SPACE_SM)

        # 1. Suggestion / Recommendation Box
        if item.suggestion:
            sug_frame = QFrame(self)
            sug_frame.setProperty("card_elevated", "true")
            sug_layout = QHBoxLayout(sug_frame)
            sug_layout.setContentsMargins(tokens.SPACE_SM, tokens.SPACE_XS, tokens.SPACE_SM, tokens.SPACE_XS)
            sug_icon = QLabel()
            sug_icon.setPixmap(icons.get_tinted_pixmap("info", color_hex=tokens.ACCENT, size=16))
            sug_layout.addWidget(sug_icon)
            sug_text = QLabel(f"<b>Suggested Fix:</b> {item.suggestion}")
            sug_text.setWordWrap(True)
            sug_layout.addWidget(sug_text, 1)
            self._detail_layout.addWidget(sug_frame)

        # 2. Evidence Table (file | field | raw value + copy)
        if item.evidence:
            ev_lbl = QLabel("EVIDENCE")
            ev_lbl.setProperty("section_header", "true")
            self._detail_layout.addWidget(ev_lbl)

            self._evidence_table = QTableWidget(len(item.evidence), 3, self)
            self._evidence_table.setHorizontalHeaderLabels([
                strings.ISSUES_EVIDENCE_HEADER_FILE,
                strings.ISSUES_EVIDENCE_HEADER_FIELD,
                strings.ISSUES_EVIDENCE_HEADER_VALUE,
            ])
            self._evidence_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self._evidence_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self._evidence_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self._evidence_table.verticalHeader().setVisible(False)
            self._evidence_table.setFrameShape(QFrame.Shape.NoFrame)
            self._evidence_table.setShowGrid(False)
            self._evidence_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._evidence_table.setObjectName("evidenceTable")
            self._evidence_table.setStyleSheet(f"""
                QTableWidget#evidenceTable {{
                    background-color: transparent;
                    border: none;
                }}
                QTableWidget#evidenceTable::item {{
                    padding: 0px 8px;
                    border-bottom: 1px solid {tokens.BORDER_HAIRLINE};
                }}
            """)

            for row_idx, ev in enumerate(item.evidence):
                f_item = QTableWidgetItem(ev.file or "-")
                f_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                f_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._evidence_table.setItem(row_idx, 0, f_item)

                field_item = QTableWidgetItem(ev.field or "-")
                field_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                field_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._evidence_table.setItem(row_idx, 1, field_item)

                val_widget = QWidget()
                val_layout = QHBoxLayout(val_widget)
                val_layout.setContentsMargins(0, 0, 0, 0)
                val_layout.setSpacing(tokens.SPACE_XS)
                val_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

                raw_val = str(ev.raw_value or ev.message or "-")
                v_lbl = QLabel(raw_val)
                v_lbl.setProperty("code", "true")
                v_lbl.setWordWrap(True)
                v_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                val_layout.addWidget(v_lbl, 1)

                if ev.raw_value:
                    cp_btn = QPushButton(strings.ISSUES_COPY_EVIDENCE_BTN)
                    cp_btn.setProperty("ghost", "true")
                    cp_btn.setFixedHeight(26)
                    cp_btn.setIcon(icons.get_tinted_icon("package", color_hex=tokens.TEXT_SECONDARY, size=12))
                    cp_btn.setToolTip("Copy raw value to clipboard")
                    cp_btn.clicked.connect(lambda _, text=raw_val: self._copy_to_clipboard(text))
                    val_layout.addWidget(cp_btn)

                self._evidence_table.setCellWidget(row_idx, 2, val_widget)
                row_h = max(36, val_widget.sizeHint().height())
                self._evidence_table.setRowHeight(row_idx, row_h)

            header_h = max(self._evidence_table.horizontalHeader().sizeHint().height(), 32)
            shown_rows = min(len(item.evidence), 6)
            table_content_h = sum(self._evidence_table.rowHeight(r) for r in range(shown_rows))
            self._evidence_table.setFixedHeight(header_h + table_content_h + 8)

            self._detail_layout.addWidget(self._evidence_table)

        # 3. Action row: Related Mods + Show in Mods button
        action_row = QHBoxLayout()
        action_row.setSpacing(tokens.SPACE_SM)

        if item.related_mods:
            mods_lbl = QLabel(f"Affected mods: <b>{', '.join(item.related_mods)}</b>")
            mods_lbl.setProperty("caption", "true")
            action_row.addWidget(mods_lbl)
        else:
            pack_lbl = QLabel("Pack-level issue (not isolated to a single mod)")
            pack_lbl.setProperty("caption", "true")
            action_row.addWidget(pack_lbl)

        action_row.addStretch()

        if item.related_mods:
            primary_mod = item.related_mods[0]
            show_mods_btn = QPushButton(strings.ISSUES_SHOW_IN_MODS_BTN)
            show_mods_btn.setIcon(icons.get_tinted_icon("mods", color_hex=tokens.TEXT_PRIMARY, size=14))
            show_mods_btn.clicked.connect(lambda _, m=primary_mod: self.show_in_mods_clicked.emit(m))
            action_row.addWidget(show_mods_btn)

        self._detail_layout.addLayout(action_row)
        self._detail_container.setVisible(False)
        self._main_layout.addWidget(self._detail_container)

        self._main_layout.activate()
        self.setFixedHeight(self._main_layout.sizeHint().height())

    def sizeHint(self) -> QSize:
        return self._main_layout.sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self._main_layout.sizeHint()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._is_expanded:
            self._main_layout.activate()
            h = self._main_layout.sizeHint().height()
            if self.height() != h:
                self.setFixedHeight(h)

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._is_expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._is_expanded = expanded
        self._detail_container.setVisible(expanded)
        icon_name = "chevron_down" if not expanded else "clear"
        self._toggle_btn.setIcon(icons.get_tinted_icon(icon_name, color_hex=tokens.TEXT_SECONDARY, size=16))
        self._main_layout.activate()
        self.setFixedHeight(self._main_layout.sizeHint().height())
        self.updateGeometry()

    def is_expanded(self) -> bool:
        return self._is_expanded

    def _copy_to_clipboard(self, text: str) -> None:
        cb = QApplication.clipboard()
        if cb:
            cb.setText(text)


class IssuesPage(QWidget):
    """Diagnostic Issues page with search, severity/certainty chips, and detail expansion."""

    scan_requested = Signal()
    navigate_to_mod = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_items: list[IssueItem] = []
        self._filtered_items: list[IssueItem] = []
        self._active_record: ScanRecord | None = None

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._main_layout.setSpacing(tokens.SPACE_MD)

        # Header Title and Subtitle
        header_widget = QWidget(self)
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(tokens.SPACE_XXS)

        self._title_label = QLabel(strings.ISSUES_TITLE)
        self._title_label.setProperty("heading", "true")
        title_font = self._title_label.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)
        header_layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.ISSUES_SUBTITLE)
        self._subtitle_label.setProperty("subheading", "true")
        header_layout.addWidget(self._subtitle_label)

        self._main_layout.addWidget(header_widget)

        # Filter Controls Bar
        self._filter_container = QWidget(self)
        filter_col = QVBoxLayout(self._filter_container)
        filter_col.setContentsMargins(0, 0, 0, 0)
        filter_col.setSpacing(tokens.SPACE_SM)

        # Row 1: Search Input + Category Combo + Group By Combo
        row1 = QHBoxLayout()
        row1.setSpacing(tokens.SPACE_MD)

        self._search_input = SearchInput(strings.ISSUES_SEARCH_PLACEHOLDER, self)
        self._search_input.search_changed.connect(self._apply_filters)
        row1.addWidget(self._search_input, 2)

        self._cat_combo = QComboBox(self)
        self._cat_combo.addItems([
            strings.ISSUES_FILTER_ALL_CATEGORIES,
            "Metadata",
            "Dependency",
            "Conflict",
            "Crash",
        ])
        self._cat_combo.currentTextChanged.connect(self._apply_filters)
        row1.addWidget(self._cat_combo, 1)

        self._group_combo = QComboBox(self)
        self._group_combo.addItems([
            strings.ISSUES_GROUP_NONE,
            strings.ISSUES_GROUP_CATEGORY,
            strings.ISSUES_GROUP_SEVERITY,
            strings.ISSUES_GROUP_MOD,
        ])
        self._group_combo.currentTextChanged.connect(self._apply_filters)
        row1.addWidget(self._group_combo, 1)

        self._pack_only_check = QCheckBox(strings.ISSUES_FILTER_PACK_LEVEL, self)
        self._pack_only_check.toggled.connect(self._apply_filters)
        row1.addWidget(self._pack_only_check)

        filter_col.addLayout(row1)

        # Row 2: Severity and Certainty Filter Chips
        chips_row = QHBoxLayout()
        chips_row.setSpacing(tokens.SPACE_SM)

        # Severity chips
        self._chip_error = FilterChip("error", "Error", 0, color_role="error", parent=self)
        self._chip_error.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_error)

        self._chip_warning = FilterChip("warning", "Warning", 0, color_role="warning", parent=self)
        self._chip_warning.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_warning)

        self._chip_info = FilterChip("info", "Info", 0, color_role="info", parent=self)
        self._chip_info.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_info)

        chips_row.addSpacing(tokens.SPACE_MD)

        # Certainty chips
        self._chip_certain = FilterChip("Certain", strings.ISSUES_CERTAIN, 0, color_role="accent", parent=self)
        self._chip_certain.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_certain)

        self._chip_possible = FilterChip("Possible", strings.ISSUES_POSSIBLE, 0, color_role="neutral", parent=self)
        self._chip_possible.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_possible)

        self._chip_insufficient = FilterChip("Insufficient data", strings.ISSUES_INSUFFICIENT, 0, color_role="neutral", parent=self)
        self._chip_insufficient.toggled.connect(self._apply_filters)
        chips_row.addWidget(self._chip_insufficient)

        chips_row.addStretch()
        filter_col.addLayout(chips_row)

        # Row 3: One-line Certainty Legend
        legend_lbl = QLabel(strings.ISSUES_LEGEND_TEXT)
        legend_lbl.setProperty("caption", "true")
        legend_lbl.setWordWrap(True)
        legend_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
        filter_col.addWidget(legend_lbl)

        self._main_layout.addWidget(self._filter_container)

        # Content Scroll Area for Cards
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._scroll_layout.setContentsMargins(0, 0, tokens.SPACE_SM, 0)
        self._scroll_layout.setSpacing(tokens.SPACE_SM)
        self._scroll_layout.addStretch()

        self._scroll_area.setWidget(self._scroll_content)
        self._main_layout.addWidget(self._scroll_area, 1)

        # Empty State Card (for Clean pack, Never Scanned, No Profiles, No filter match)
        self._state_card = StateCard(self)
        self._main_layout.addWidget(self._state_card, 1)

        self._issue_cards: list[IssueCard] = []
        self.set_state(PageState.EMPTY)

    @property
    def title_label(self) -> QLabel:
        return self._title_label

    @property
    def subtitle_label(self) -> QLabel:
        return self._subtitle_label

    def set_state(self, state: PageState) -> None:
        if state == PageState.READY:
            self._state_card.setVisible(False)
            self._filter_container.setVisible(True)
            self._scroll_area.setVisible(True)
        else:
            self._scroll_area.setVisible(False)
            self._filter_container.setVisible(False)
            self._state_card.setVisible(True)

    def show_empty_no_profiles(self) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=strings.STATE_EMPTY_TITLE,
            description=strings.DASHBOARD_EMPTY_DESC,
            icon_name="folder",
        )

    def show_never_scanned(self, profile_name: str) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=f"'{profile_name}' {strings.PROFILES_CARD_NEVER_SCANNED}",
            description=strings.ISSUES_NEVER_SCANNED_DESC,
            action_text=strings.STATE_ACTION_SCAN_NOW,
            on_action=self.scan_requested.emit,
            icon_name="scan",
        )

    def show_loading(self) -> None:
        self.set_state(PageState.LOADING)
        self._state_card.show_loading(
            title=strings.STATE_LOADING_TITLE,
            description=strings.STATE_LOADING_DESC,
        )

    def show_error(self, code: str, message: str) -> None:
        self.set_state(PageState.ERROR)
        self._state_card.show_error(
            title=strings.STATE_ERROR_TITLE,
            description=message,
            code=code,
            action_text=strings.STATE_ACTION_RETRY,
            on_action=self.scan_requested.emit,
        )

    def set_data(self, record: ScanRecord) -> None:
        self._active_record = record
        self._all_items = build_issues_view(record)

        if not self._all_items:
            # Clean pack: 0 diagnostic issues detected
            self.set_state(PageState.EMPTY)
            self._state_card.show_empty(
                title=strings.ISSUES_EMPTY_CLEAN_TITLE,
                description=strings.ISSUES_EMPTY_CLEAN_DESC,
                icon_name="check",
            )
            return

        self.set_state(PageState.READY)
        self._update_chip_counts()
        self._apply_filters()

    def _update_chip_counts(self) -> None:
        err_c = sum(1 for it in self._all_items if it.severity == "error")
        warn_c = sum(1 for it in self._all_items if it.severity == "warning")
        info_c = sum(1 for it in self._all_items if it.severity == "info")
        self._chip_error.set_count(err_c)
        self._chip_warning.set_count(warn_c)
        self._chip_info.set_count(info_c)

        cert_c = sum(1 for it in self._all_items if it.certainty == "Certain")
        poss_c = sum(1 for it in self._all_items if it.certainty == "Possible")
        insuf_c = sum(1 for it in self._all_items if it.certainty == "Insufficient data")
        self._chip_certain.set_count(cert_c)
        self._chip_possible.set_count(poss_c)
        self._chip_insufficient.set_count(insuf_c)

    def reset_filters(self) -> None:
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)

        self._cat_combo.blockSignals(True)
        self._cat_combo.setCurrentIndex(0)
        self._cat_combo.blockSignals(False)

        self._group_combo.blockSignals(True)
        self._group_combo.setCurrentIndex(0)
        self._group_combo.blockSignals(False)

        self._pack_only_check.blockSignals(True)
        self._pack_only_check.setChecked(False)
        self._pack_only_check.blockSignals(False)

        for chip in (self._chip_error, self._chip_warning, self._chip_info, self._chip_certain, self._chip_possible, self._chip_insufficient):
            chip.blockSignals(True)
            chip.setChecked(True)
            chip.blockSignals(False)

        self._apply_filters()

    def _clear_scroll_layout(self) -> None:
        while self._scroll_layout.count() > 1:
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sw = sub.widget()
                        sw.hide()
                        sw.setParent(None)
                        sw.deleteLater()
        self._issue_cards.clear()

    def _apply_filters(self) -> None:
        if not self._all_items:
            return

        active_sevs = set()
        if self._chip_error.isChecked():
            active_sevs.add("error")
        if self._chip_warning.isChecked():
            active_sevs.add("warning")
        if self._chip_info.isChecked():
            active_sevs.add("info")

        active_certs = set()
        if self._chip_certain.isChecked():
            active_certs.add("Certain")
        if self._chip_possible.isChecked():
            active_certs.add("Possible")
        if self._chip_insufficient.isChecked():
            active_certs.add("Insufficient data")

        cat_txt = self._cat_combo.currentText().strip().lower()
        if cat_txt == strings.ISSUES_FILTER_ALL_CATEGORIES.lower() or not cat_txt:
            active_cats = {"metadata", "dependency", "conflict", "crash"}
        else:
            active_cats = {cat_txt}

        criteria = IssueFilterCriteria(
            severities=active_sevs,
            certainties=active_certs,
            categories=active_cats,
            search_text=self._search_input.text(),
            pack_level_only=self._pack_only_check.isChecked(),
        )

        self._filtered_items = filter_issues(self._all_items, criteria)
        self._clear_scroll_layout()

        if not self._filtered_items:
            empty_frame = QFrame(self)
            empty_frame.setProperty("card", "true")
            ef_layout = QVBoxLayout(empty_frame)
            ef_layout.setContentsMargins(tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL, tokens.SPACE_XL)
            ef_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ef_layout.setSpacing(tokens.SPACE_MD)

            icon_lbl = QLabel()
            icon_lbl.setPixmap(icons.get_tinted_pixmap("filter", color_hex=tokens.TEXT_MUTED, size=40))
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ef_layout.addWidget(icon_lbl)

            t_lbl = QLabel(strings.ISSUES_EMPTY_FILTER_TITLE)
            t_lbl.setProperty("heading", "true")
            t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ef_layout.addWidget(t_lbl)

            d_lbl = QLabel(strings.ISSUES_EMPTY_FILTER_DESC)
            d_lbl.setProperty("subheading", "true")
            d_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ef_layout.addWidget(d_lbl)

            reset_btn = QPushButton(strings.MODS_RESET_FILTERS_BTN)
            reset_btn.clicked.connect(self.reset_filters)
            ef_layout.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignCenter)

            self._scroll_layout.insertWidget(0, empty_frame)
            return

        # Handle grouping
        group_sel = self._group_combo.currentText()
        group_mode = "none"
        if group_sel == strings.ISSUES_GROUP_CATEGORY:
            group_mode = "category"
        elif group_sel == strings.ISSUES_GROUP_SEVERITY:
            group_mode = "severity"
        elif group_sel == strings.ISSUES_GROUP_MOD:
            group_mode = "mod"

        grouped = group_issues(self._filtered_items, group_mode)

        insert_idx = 0
        for group_name, items in grouped.items():
            if group_mode != "none":
                group_lbl = QLabel(f"{group_name.upper()} ({len(items)})")
                group_lbl.setProperty("section_header", "true")
                group_lbl.setStyleSheet(f"font-size: {tokens.FONT_SIZE_SM}px; font-weight: {tokens.FONT_WEIGHT_BOLD}; color: {tokens.ACCENT};")
                self._scroll_layout.insertWidget(insert_idx, group_lbl)
                insert_idx += 1

            for it in items:
                card = IssueCard(it, self._scroll_content)
                card.show_in_mods_clicked.connect(self.navigate_to_mod.emit)
                self._issue_cards.append(card)
                self._scroll_layout.insertWidget(insert_idx, card)
                insert_idx += 1

        self._scroll_layout.activate()
        hint = self._scroll_layout.sizeHint()
        self._scroll_content.resize(self._scroll_content.width(), hint.height())
