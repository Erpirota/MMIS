"""Mods page with virtualized table, filtering, sorting, and detail drawer."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from mmis.domain.models import Dependency, Finding, Loader, ModInfo, ScanResult, Severity
from mmis.domain.profiles import ScanRecord
from mmis.services.pack_view import FilterCriteria, ModRow, build_pack_view, filter_and_sort
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.search_input import SearchInput
from mmis.ui.widgets.state_card import PageState, StateCard
from mmis.ui.widgets.status_pill import StatusPillDelegate, StatusPillWidget

# Table column definitions: Status | Name | Mod ID | Version | Loader | Issues | Jar file
COLUMNS = [
    (strings.MODS_COL_STATUS, 95),
    (strings.MODS_COL_NAME, 240),
    (strings.MODS_COL_ID, 180),
    (strings.MODS_COL_VERSION, 110),
    (strings.MODS_COL_LOADER, 100),
    (strings.MODS_COL_ISSUES, 75),
    (strings.MODS_COL_JAR, 220),
]


class ModTableModel(QAbstractTableModel):
    """Virtualized table model adapting pure ModRow presentation data."""

    def __init__(self, rows: list[ModRow] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ModRow] = list(rows or [])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None

        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return row.status
            elif col == 1:
                return f"{row.name} (nested)" if row.is_nested else row.name
            elif col == 2:
                return row.mod_id
            elif col == 3:
                return row.version
            elif col == 4:
                return row.loader
            elif col == 5:
                return str(row.issue_count) if row.issue_count > 0 else "-"
            elif col == 6:
                return f"{row.jar_name} (disabled)" if row.is_disabled else row.jar_name
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 5):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 1 and row.is_disabled:
                return QColor(tokens.COLOR_DISABLED)
            if col == 5 and row.issue_count > 0:
                return QColor(tokens.COLOR_ERROR if row.error_count > 0 else tokens.COLOR_WARNING)
            return QColor(tokens.TEXT_PRIMARY)
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == 0:
                return f"Status: {row.status.upper()}"
            elif col == 1:
                extra = " (nested mod)" if row.is_nested else ""
                return f"{row.name}{extra}"
            elif col == 2:
                return f"Mod ID: {row.mod_id}"
            elif col == 3:
                return f"Version: {row.version}"
            elif col == 4:
                return f"Loader: {row.loader}"
            elif col == 5:
                if row.issue_count > 0:
                    return f"{row.issue_count} issue(s) ({row.error_count} error(s), {row.warning_count} warning(s))"
                return "No issues detected"
            elif col == 6:
                status_extra = " [Disabled]" if row.is_disabled else ""
                return f"{row.jar_name}{status_extra}\nPath: {row.jar_path}"

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                if 0 <= section < len(COLUMNS):
                    return COLUMNS[section][0]
            elif role == Qt.ItemDataRole.TextAlignmentRole:
                if section in (0, 5):
                    return int(Qt.AlignmentFlag.AlignCenter)
                return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def get_row(self, index: int) -> ModRow | None:
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def set_rows(self, rows: list[ModRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()


class ScrimWidget(QWidget):
    """Semi-transparent scrim overlay for drawer backdrop."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background-color: rgba(15, 17, 21, 0.70);")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class DetailDrawer(QFrame):
    """Right-side detail inspection drawer displaying mod metadata, deps, and issues.
    
    Section order strictly follows design guidelines:
    1. Header (name, version, status pill, id)
    2. Findings (pill + message + suggestion)
    3. Dependencies (satisfied / missing / wrong version / optional)
    4. Metadata (2-column key/value grid)
    5. Related files
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailDrawer")
        self.setProperty("card_elevated", "true")
        self.setMinimumWidth(380)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        main_layout.setSpacing(tokens.SPACE_MD)

        # Header: Title + Close Button
        header_layout = QHBoxLayout()
        title_lbl = QLabel(strings.DRAWER_TITLE)
        title_lbl.setProperty("section_header", "true")
        header_layout.addWidget(title_lbl)

        header_layout.addStretch()

        close_btn = QPushButton()
        close_btn.setProperty("ghost", "true")
        close_btn.setIcon(icons.get_tinted_icon("close", color_hex=tokens.TEXT_MUTED, size=16))
        close_btn.setToolTip(strings.DRAWER_CLOSE_TOOLTIP)
        close_btn.clicked.connect(self.closed.emit)
        header_layout.addWidget(close_btn)

        main_layout.addLayout(header_layout)

        # Scroll area for details (vertical only, no horizontal scroll)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, tokens.SPACE_SM, 0)
        self._content_layout.setSpacing(tokens.SPACE_LG)

        # 1. Header Section: Mod Title, Status Pill, ID
        self._mod_title = QLabel("Select a mod")
        self._mod_title.setProperty("heading", "true")
        self._mod_title.setWordWrap(True)
        title_font = self._mod_title.font()
        title_font.setPointSize(tokens.FONT_SIZE_LG)
        title_font.setWeight(QFont.Weight.Bold)
        self._mod_title.setFont(title_font)
        self._content_layout.addWidget(self._mod_title)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(tokens.SPACE_SM)
        self._status_pill = StatusPillWidget("ok", self)
        meta_row.addWidget(self._status_pill)

        self._mod_id_lbl = QLabel()
        self._mod_id_lbl.setProperty("code", "true")
        self._mod_id_lbl.setWordWrap(True)
        meta_row.addWidget(self._mod_id_lbl)
        meta_row.addStretch()
        self._content_layout.addLayout(meta_row)

        # 2. Findings Section
        self._findings_header = self._create_section_label(strings.DRAWER_ISSUES_HEADER)
        self._content_layout.addWidget(self._findings_header)
        self._findings_box = QVBoxLayout()
        self._findings_box.setSpacing(tokens.SPACE_SM)
        self._content_layout.addLayout(self._findings_box)

        # 3. Dependencies Section
        self._deps_header = self._create_section_label(strings.DRAWER_DEPENDENCIES_HEADER)
        self._content_layout.addWidget(self._deps_header)
        self._deps_box = QVBoxLayout()
        self._deps_box.setSpacing(tokens.SPACE_XS)
        self._content_layout.addLayout(self._deps_box)

        # 4. Metadata Section (2-column key/value grid)
        self._meta_header = self._create_section_label(strings.DRAWER_METADATA_HEADER)
        self._content_layout.addWidget(self._meta_header)
        self._meta_grid = QGridLayout()
        self._meta_grid.setHorizontalSpacing(tokens.SPACE_MD)
        self._meta_grid.setVerticalSpacing(tokens.SPACE_XS)
        self._content_layout.addLayout(self._meta_grid)

        # Alternative formats section (Dual loader)
        self._formats_header = self._create_section_label(strings.DRAWER_FORMATS_HEADER)
        self._content_layout.addWidget(self._formats_header)
        self._formats_box = QVBoxLayout()
        self._formats_box.setSpacing(tokens.SPACE_XS)
        self._content_layout.addLayout(self._formats_box)

        # 5. Related Files Section
        self._files_header = self._create_section_label(strings.DRAWER_FILES_HEADER)
        self._content_layout.addWidget(self._files_header)
        self._files_box = QVBoxLayout()
        self._files_box.setSpacing(tokens.SPACE_XXS)
        self._content_layout.addLayout(self._files_box)

        self._content_layout.addStretch()
        scroll.setWidget(self._content)
        main_layout.addWidget(scroll)

    def _create_section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setProperty("section_header", "true")
        return lbl

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _get_dep_status(self, dep: Dependency, related_findings: list[Finding]) -> tuple[str, str]:
        """Classify dependency status: (label, status_kind)."""
        if dep.kind.value == "optional":
            return "Optional", "disabled"
        for f in related_findings:
            rel = [m.lower() for m in f.related_mod_ids]
            if dep.mod_id.lower() in rel or dep.mod_id.lower() in f.message.lower():
                if "MISSING" in f.code:
                    return "Missing", "error"
                if "VERSION" in f.code or "CONFLICT" in f.code:
                    return "Wrong Version", "warning"
        return "Satisfied", "ok"

    def show_mod(self, row: ModRow) -> None:
        self._mod_title.setText(f"{row.name} v{row.version}")
        self._mod_id_lbl.setText(row.mod_id)
        self._status_pill.set_status(row.status)

        # 2. Findings Section
        self._clear_layout(self._findings_box)
        if not row.related_findings:
            no_issues = QLabel(strings.DRAWER_NO_ISSUES)
            no_issues.setProperty("caption", "true")
            no_issues.setWordWrap(True)
            self._findings_box.addWidget(no_issues)
        else:
            for f in row.related_findings:
                box = QFrame()
                box.setProperty("card_elevated", "true")
                bl = QVBoxLayout(box)
                bl.setContentsMargins(tokens.SPACE_SM, tokens.SPACE_SM, tokens.SPACE_SM, tokens.SPACE_SM)
                bl.setSpacing(tokens.SPACE_XXS)

                top_row = QHBoxLayout()
                sev_pill = StatusPillWidget(f.severity.value, self)
                top_row.addWidget(sev_pill)
                code_lbl = QLabel(f.code)
                code_lbl.setProperty("code", "true")
                code_lbl.setWordWrap(True)
                top_row.addWidget(code_lbl)
                top_row.addStretch()
                bl.addLayout(top_row)

                msg_l = QLabel(f.message)
                msg_l.setProperty("subheading", "true")
                msg_l.setWordWrap(True)
                bl.addWidget(msg_l)

                if f.suggestion:
                    sug_l = QLabel(f"{strings.DRAWER_SUGGESTION_LABEL} {f.suggestion}")
                    sug_l.setProperty("caption", "true")
                    sug_l.setWordWrap(True)
                    bl.addWidget(sug_l)

                self._findings_box.addWidget(box)

        # 3. Dependencies Section
        self._clear_layout(self._deps_box)
        if not row.dependencies:
            no_deps = QLabel(strings.DRAWER_NO_DEPENDENCIES)
            no_deps.setProperty("caption", "true")
            no_deps.setWordWrap(True)
            self._deps_box.addWidget(no_deps)
        else:
            for d in row.dependencies:
                status_text, status_kind = self._get_dep_status(d, row.related_findings)
                d_box = QHBoxLayout()
                d_box.setSpacing(tokens.SPACE_SM)

                d_pill = StatusPillWidget(status_kind, self)
                d_box.addWidget(d_pill)

                raw_vr = d.version_range.raw if hasattr(d.version_range, "raw") else str(d.version_range or "")
                vr = f" ({raw_vr})" if raw_vr else ""
                dep_lbl = QLabel(f"<b>{d.mod_id}</b>{vr} &mdash; {status_text}")
                dep_lbl.setProperty("subheading", "true")
                dep_lbl.setWordWrap(True)
                d_box.addWidget(dep_lbl, 1)

                self._deps_box.addLayout(d_box)

        # 4. Metadata Section (2-Column Key/Value Grid)
        self._clear_layout(self._meta_grid)
        meta_items = [
            (strings.DRAWER_JAR_LABEL, row.jar_name),
            ("Full Path:", row.jar_path),
            ("Loader:", row.loader),
            (strings.DRAWER_ENV_LABEL, row.environment),
            (strings.DRAWER_LICENSE_LABEL, row.license),
            (strings.DRAWER_AUTHORS_LABEL, ", ".join(row.authors) if row.authors else strings.STATE_UNKNOWN),
            (strings.DRAWER_DESCRIPTION_LABEL, row.description),
        ]
        for grid_row, (k, v) in enumerate(meta_items):
            k_lbl = QLabel(f"{k}")
            k_lbl.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};")
            k_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            v_lbl = QLabel(v)
            v_lbl.setProperty("subheading", "true")
            v_lbl.setWordWrap(True)
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            self._meta_grid.addWidget(k_lbl, grid_row, 0)
            self._meta_grid.addWidget(v_lbl, grid_row, 1)

        self._meta_grid.setColumnStretch(0, 0)
        self._meta_grid.setColumnStretch(1, 1)

        # Populate Alternative Formats (Dual loader)
        self._clear_layout(self._formats_box)
        if row.other_formats:
            self._formats_header.setVisible(True)
            for of in row.other_formats:
                fmt_lbl = QLabel(f"• Format: {of.metadata_file} | Loaders: {', '.join(l.value for l in of.loaders)} | Ver: {of.version or 'Unknown'}")
                fmt_lbl.setProperty("caption", "true")
                fmt_lbl.setWordWrap(True)
                self._formats_box.addWidget(fmt_lbl)
        else:
            self._formats_header.setVisible(False)

        # 5. Related Files Section
        self._clear_layout(self._files_box)
        if not row.related_files:
            no_f = QLabel(strings.DRAWER_NO_FILES)
            no_f.setProperty("caption", "true")
            no_f.setWordWrap(True)
            self._files_box.addWidget(no_f)
        else:
            for rf in row.related_files:
                rf_lbl = QLabel(f"• {rf}")
                rf_lbl.setProperty("code", "true")
                rf_lbl.setWordWrap(True)
                self._files_box.addWidget(rf_lbl)


class ModsPage(QWidget):
    """Main installed mods view with table, search/filter controls, and detail drawer."""

    scan_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_rows: list[ModRow] = []
        self._filtered_rows: list[ModRow] = []
        self._criteria = FilterCriteria()
        self._is_drawer_open = False

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._main_layout.setSpacing(tokens.SPACE_MD)

        # Top Header & Filter Controls
        header_layout = QVBoxLayout()
        header_layout.setSpacing(tokens.SPACE_XS)

        title_lbl = QLabel(strings.MODS_TITLE)
        title_lbl.setProperty("heading", "true")
        title_font = title_lbl.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        title_lbl.setFont(title_font)
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel(strings.MODS_SUBTITLE)
        sub_lbl.setProperty("subheading", "true")
        header_layout.addWidget(sub_lbl)

        self._main_layout.addLayout(header_layout)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(tokens.SPACE_MD)

        # Search field
        self._search_input = SearchInput(strings.MODS_SEARCH_PLACEHOLDER, self)
        self._search_input.search_changed.connect(self._on_search_changed)
        filter_bar.addWidget(self._search_input, 2)

        # Status filter combo
        self._status_combo = QComboBox()
        self._status_combo.addItems([
            strings.MODS_STATUS_ALL,
            strings.MODS_STATUS_OK,
            strings.MODS_STATUS_WARNING,
            strings.MODS_STATUS_ERROR,
            strings.MODS_STATUS_DISABLED,
        ])
        self._status_combo.currentTextChanged.connect(self._on_status_changed)
        filter_bar.addWidget(self._status_combo, 1)

        # Loader filter combo
        self._loader_combo = QComboBox()
        self._loader_combo.addItems([
            strings.MODS_FILTER_ALL,
            "Fabric",
            "Quilt",
            "Forge",
            "NeoForge",
        ])
        self._loader_combo.currentTextChanged.connect(self._on_loader_changed)
        filter_bar.addWidget(self._loader_combo, 1)

        # Issues only checkbox
        self._issues_check = QCheckBox(strings.MODS_FILTER_ISSUES_ONLY)
        self._issues_check.toggled.connect(self._on_issues_toggled)
        filter_bar.addWidget(self._issues_check)

        # Mod count badge
        self._count_label = QLabel()
        self._count_label.setProperty("subheading", "true")
        filter_bar.addWidget(self._count_label)

        self._main_layout.addLayout(filter_bar)

        # State card (for empty profile / never scanned)
        self._state_card = StateCard(self)
        self._main_layout.addWidget(self._state_card, 1)

        # Main splitter holding Table + Detail Drawer (for split pane mode >= 1200 px)
        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)

        # Table View Container
        self._table_container = QWidget()
        table_layout = QVBoxLayout(self._table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(tokens.SPACE_SM)

        self._table = QTableView(self)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        self._model = ModTableModel([], self)
        self._table.setModel(self._model)

        self._pill_delegate = StatusPillDelegate(self)
        self._table.setItemDelegateForColumn(0, self._pill_delegate)

        # Set column widths & resize modes
        for idx, (_, width) in enumerate(COLUMNS):
            self._table.setColumnWidth(idx, width)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self._table.selectionModel().selectionChanged.connect(self._on_row_selected)
        self._table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)

        table_layout.addWidget(self._table)

        # Empty result state card (when filter yields 0 matches)
        self._empty_filter_card = StateCard(self)
        table_layout.addWidget(self._empty_filter_card)
        self._empty_filter_card.setVisible(False)

        self._splitter.addWidget(self._table_container)

        # Detail Drawer instance
        self._drawer = DetailDrawer(self)
        self._drawer.closed.connect(self.close_drawer)
        self._drawer.setVisible(False)
        self._splitter.addWidget(self._drawer)

        self._main_layout.addWidget(self._splitter, 1)

        # Overlay widget with scrim (for < 1200 px responsive mode)
        self._overlay_widget = QWidget(self)
        self._overlay_widget.setVisible(False)
        self._overlay_layout = QHBoxLayout(self._overlay_widget)
        self._overlay_layout.setContentsMargins(0, 0, 0, 0)
        self._overlay_layout.setSpacing(0)

        self._scrim = ScrimWidget(self._overlay_widget)
        self._scrim.clicked.connect(self.close_drawer)
        self._overlay_layout.addWidget(self._scrim, 1)

        self.set_state(PageState.EMPTY)

    def set_state(self, state: PageState) -> None:
        if state == PageState.READY:
            self._state_card.setVisible(False)
            self._splitter.setVisible(True)
            self.update_column_visibility()
        else:
            self._splitter.setVisible(False)
            self._state_card.setVisible(True)

    def show_never_scanned(self, profile_name: str) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=f"'{profile_name}' {strings.PROFILES_CARD_NEVER_SCANNED}",
            description=strings.MODS_NEVER_SCANNED_DESC,
            action_text=strings.STATE_ACTION_SCAN_NOW,
            on_action=self.scan_requested.emit,
            icon_name="scan",
        )

    def show_empty_no_profiles(self) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=strings.STATE_EMPTY_TITLE,
            description=strings.DASHBOARD_EMPTY_DESC,
            icon_name="folder",
        )

    def show_loading(self) -> None:
        self.set_state(PageState.LOADING)
        self._state_card.show_loading()

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
        self.set_state(PageState.READY)
        self._all_rows = build_pack_view(record.result, record.analysis)
        self.reset_filters()

    def reset_filters(self) -> None:
        """Reset search and filter inputs to defaults."""
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)

        self._status_combo.blockSignals(True)
        self._status_combo.setCurrentIndex(0)
        self._status_combo.blockSignals(False)

        self._loader_combo.blockSignals(True)
        self._loader_combo.setCurrentIndex(0)
        self._loader_combo.blockSignals(False)

        self._issues_check.blockSignals(True)
        self._issues_check.setChecked(False)
        self._issues_check.blockSignals(False)

        self._criteria = FilterCriteria()
        self._apply_filters()

    def focus_search(self) -> None:
        self._search_input.setFocus()
        self._search_input.selectAll()

    def _on_search_changed(self, text: str) -> None:
        self._criteria = FilterCriteria(
            search_text=text,
            loader=self._criteria.loader,
            version=self._criteria.version,
            status=self._criteria.status,
            has_issues=self._criteria.has_issues,
            sort_key=self._criteria.sort_key,
            sort_ascending=self._criteria.sort_ascending,
        )
        self._apply_filters()

    def _on_status_changed(self, text: str) -> None:
        status_val = None if text == strings.MODS_STATUS_ALL else text.lower()
        self._criteria = FilterCriteria(
            search_text=self._criteria.search_text,
            loader=self._criteria.loader,
            version=self._criteria.version,
            status=status_val,
            has_issues=self._criteria.has_issues,
            sort_key=self._criteria.sort_key,
            sort_ascending=self._criteria.sort_ascending,
        )
        self._apply_filters()

    def _on_loader_changed(self, text: str) -> None:
        loader_val = None if text == strings.MODS_FILTER_ALL else text.lower()
        self._criteria = FilterCriteria(
            search_text=self._criteria.search_text,
            loader=loader_val,
            version=self._criteria.version,
            status=self._criteria.status,
            has_issues=self._criteria.has_issues,
            sort_key=self._criteria.sort_key,
            sort_ascending=self._criteria.sort_ascending,
        )
        self._apply_filters()

    def _on_issues_toggled(self, checked: bool) -> None:
        self._criteria = FilterCriteria(
            search_text=self._criteria.search_text,
            loader=self._criteria.loader,
            version=self._criteria.version,
            status=self._criteria.status,
            has_issues=True if checked else None,
            sort_key=self._criteria.sort_key,
            sort_ascending=self._criteria.sort_ascending,
        )
        self._apply_filters()

    def _on_sort_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        col_to_key = {
            0: "status",
            1: "name",
            2: "id",
            3: "version",
            4: "loader",
            5: "issue_count",
            6: "jar_name",
        }
        sort_key = col_to_key.get(logical_index, "name")
        self._criteria = FilterCriteria(
            search_text=self._criteria.search_text,
            loader=self._criteria.loader,
            version=self._criteria.version,
            status=self._criteria.status,
            has_issues=self._criteria.has_issues,
            sort_key=sort_key,
            sort_ascending=(order == Qt.SortOrder.AscendingOrder),
        )
        self._apply_filters()
        self.update_column_widths()

    def _apply_filters(self) -> None:
        self._filtered_rows = filter_and_sort(self._all_rows, self._criteria)
        self._model.set_rows(self._filtered_rows)

        visible = len(self._filtered_rows)
        total = len(self._all_rows)
        self._count_label.setText(strings.MODS_SHOWING_COUNT.format(visible=visible, total=total))

        if visible == 0 and total > 0:
            self._table.setVisible(False)
            self._empty_filter_card.show_empty(
                title=strings.MODS_EMPTY_FILTER_TITLE,
                description=strings.MODS_EMPTY_FILTER_DESC,
                action_text=strings.MODS_RESET_FILTERS_BTN,
                on_action=self.reset_filters,
                icon_name="filter",
            )
            self._empty_filter_card.setVisible(True)
        else:
            self._empty_filter_card.setVisible(False)
            self._table.setVisible(True)

    def _on_row_selected(self) -> None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return
        row = self._model.get_row(indexes[0].row())
        if row:
            self.open_drawer(row)

    def open_drawer(self, row: ModRow) -> None:
        self._is_drawer_open = True
        self._drawer.show_mod(row)
        self.update_layout_mode()

    def close_drawer(self) -> None:
        self._is_drawer_open = False
        self._overlay_widget.hide()
        self._drawer.hide()
        self._table.clearSelection()

    def is_drawer_open(self) -> bool:
        return self._is_drawer_open

    def update_layout_mode(self) -> None:
        """Responsive behavior: split pane at >= 1200 px, overlay with scrim below 1200 px."""
        if not self._is_drawer_open:
            self._overlay_widget.hide()
            self._drawer.hide()
            return

        window_width = self.window().width() if self.window() else self.width()

        if window_width >= 1200:
            # Mode A: Side-by-side split pane
            self._overlay_widget.hide()
            if self._drawer.parent() != self._splitter:
                self._drawer.setParent(self._splitter)
                self._splitter.addWidget(self._drawer)
            self._drawer.setMaximumWidth(16777215)
            self._drawer.setMinimumWidth(380)
            self._drawer.show()

            total = self._splitter.width()
            drawer_w = 420
            self._splitter.setSizes([max(total - drawer_w, 400), drawer_w])
        else:
            # Mode B: True overlay drawer with semi-transparent scrim
            if self._drawer.parent() != self._overlay_widget:
                self._drawer.setParent(self._overlay_widget)
                self._overlay_layout.addWidget(self._drawer)
            self._drawer.setFixedWidth(400)
            self._drawer.show()

            self._overlay_widget.setGeometry(self.rect())
            self._overlay_widget.show()
            self._overlay_widget.raise_()

    def select_mod(self, mod_id: str) -> None:
        """Find mod by ID, select its table row, scroll to it, and open detail drawer."""
        target_clean = mod_id.strip().lower()
        for idx, row in enumerate(self._filtered_rows):
            if row.mod_id.lower() == target_clean:
                self._table.selectRow(idx)
                self._table.scrollTo(self._model.index(idx, 0))
                self.open_drawer(row)
                return

        # If not visible under current filter, reset filters and retry
        self.reset_filters()
        for idx, row in enumerate(self._filtered_rows):
            if row.mod_id.lower() == target_clean:
                self._table.selectRow(idx)
                self._table.scrollTo(self._model.index(idx, 0))
                self.open_drawer(row)
                return

    def update_column_visibility(self, table_width: int | None = None) -> None:
        """Responsive column-hiding order: keeps Status + Name, hides Jar first, then Issues, then Loader, then Mod ID."""
        w = table_width if table_width is not None else self._table.width()
        if w <= 10:
            w = self.width()

        hide_jar = w < 1050
        hide_issues = w < 920
        hide_loader = w < 800
        hide_mod_id = w < 680

        self._table.setColumnHidden(6, hide_jar)      # Jar file hidden first
        self._table.setColumnHidden(5, hide_issues)   # then Issues
        self._table.setColumnHidden(4, hide_loader)   # then Loader
        self._table.setColumnHidden(2, hide_mod_id)   # then Mod ID
        # Columns 0 (Status) and 1 (Name) remain visible
        self.update_column_widths()

    def update_column_widths(self) -> None:
        """Ensure every visible column width is at least its header sectionSizeHint."""
        header = self._table.horizontalHeader()
        for idx, (_, base_width) in enumerate(COLUMNS):
            if idx == 1 or self._table.isColumnHidden(idx):
                continue
            hint = header.sectionSizeHint(idx)
            needed = max(base_width, hint)
            if header.sectionSize(idx) < needed:
                self._table.setColumnWidth(idx, needed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_column_visibility()
        if self._is_drawer_open:
            self.update_layout_mode()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update_column_visibility()
        self.update_column_widths()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self.is_drawer_open():
                self.close_drawer()
                event.accept()
                return
        super().keyPressEvent(event)
