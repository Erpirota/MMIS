"""Crash Logs page for importing, viewing, and managing crash reports and logs."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mmis.services import crash_view
from mmis.services.crash_view import CrashDetail, CrashListItem
from mmis.services.profiles import ProfileService
from mmis.ui import strings
from mmis.ui.pages.issues import CertaintyBadge, ElidedLabel
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.confirm_dialog import ConfirmDialog
from mmis.ui.widgets.state_card import PageState, StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget
from mmis.ui.workers import TaskManager


class BreakAnywhereLabel(QLabel):
    """A QLabel that wraps at word boundaries first and breaks inside tokens only when necessary."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height())

    def _target_width(self) -> int:
        if self.width() > 100:
            return self.width()
        p = self.parentWidget()
        while p is not None:
            if p.width() > 100:
                return max(100, p.width() - 32)
            p = p.parentWidget()
        return 200

    def sizeHint(self) -> QSize:
        w = self._target_width()
        return QSize(w, self.heightForWidth(w))

    def hasHeightForWidth(self) -> bool:
        return True

    def _layout_text(self, width: int) -> tuple[QTextLayout, float]:
        tl = QTextLayout(self.text(), self.font())
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        if self.alignment() & Qt.AlignmentFlag.AlignRight:
            opt.setAlignment(Qt.AlignmentFlag.AlignRight)
        elif self.alignment() & Qt.AlignmentFlag.AlignHCenter:
            opt.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        else:
            opt.setAlignment(Qt.AlignmentFlag.AlignLeft)
        tl.setTextOption(opt)
        tl.beginLayout()
        y = 0.0
        while True:
            line = tl.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, float(width)))
            line.setPosition(QPointF(0.0, y))
            y += line.height()
        tl.endLayout()
        return tl, y

    def heightForWidth(self, width: int) -> int:
        if not self.text() or width <= 0:
            return self.fontMetrics().height()
        cm = self.contentsMargins()
        avail_w = max(1, width - (cm.left() + cm.right()))
        _, h = self._layout_text(avail_w)
        return max(self.fontMetrics().height(), int(h + 0.5) + cm.top() + cm.bottom())

    def paintEvent(self, event) -> None:
        if not self.text():
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        cr = self.contentsRect()
        tl, total_h = self._layout_text(cr.width())
        top = float(cr.top())
        if self.alignment() & Qt.AlignmentFlag.AlignVCenter and cr.height() > total_h:
            top += (cr.height() - total_h) / 2.0
        elif self.alignment() & Qt.AlignmentFlag.AlignBottom and cr.height() > total_h:
            top += cr.height() - total_h
        tl.draw(painter, QPointF(float(cr.left()), top))


class CrashCard(QFrame):
    """Clickable list item card for a crash report."""

    def __init__(
        self,
        item: CrashListItem,
        is_selected: bool,
        on_select: Callable[[int], None],
        on_delete: Callable[[int], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._on_select = on_select
        self._on_delete = on_delete

        self.setProperty("card", "true")
        if is_selected:
            self.setProperty("card_elevated", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        layout.setSpacing(tokens.SPACE_XS)

        # Top row: Source Name + Delete button
        top_row = QHBoxLayout()
        top_row.setSpacing(tokens.SPACE_SM)

        self._name_label = ElidedLabel(item.source_name, self)
        name_font = self._name_label.font()
        name_font.setWeight(QFont.Weight.DemiBold)
        self._name_label.setFont(name_font)
        top_row.addWidget(self._name_label, 1)

        self._del_btn = QPushButton(self)
        self._del_btn.setIcon(icons.get_tinted_icon("trash", color_hex=tokens.TEXT_MUTED, size=14))
        self._del_btn.setFixedSize(24, 24)
        self._del_btn.setToolTip("Delete crash report")
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: {tokens.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background-color: {tokens.COLOR_ERROR_BG};
            }}
        """)
        self._del_btn.clicked.connect(lambda: self._on_delete(self.item.id))
        top_row.addWidget(self._del_btn)
        layout.addLayout(top_row)

        # Middle row: Date + Kind + Loader + Profile
        mid_row = QHBoxLayout()
        mid_row.setSpacing(tokens.SPACE_SM)

        self._date_label = QLabel(item.display_imported_at, self)
        self._date_label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
        mid_row.addWidget(self._date_label)

        self._kind_label = QLabel(item.display_kind, self)
        self._kind_label.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FONT_SIZE_XS}px;")
        mid_row.addWidget(self._kind_label)

        if item.loader:
            self._loader_label = QLabel(item.loader, self)
            self._loader_label.setStyleSheet(f"color: {tokens.ACCENT}; font-size: {tokens.FONT_SIZE_XS}px;")
            mid_row.addWidget(self._loader_label)

        self._profile_label = QLabel(item.display_profile_name, self)
        self._profile_label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
        mid_row.addWidget(self._profile_label)

        mid_row.addStretch()
        layout.addLayout(mid_row)

        # Bottom row: Top suspect or error/warning counts
        bot_row = QHBoxLayout()
        bot_row.setSpacing(tokens.SPACE_SM)

        if item.top_suspect:
            self._suspect_label = BreakAnywhereLabel(f"Suspect: {item.top_suspect}", self)
            self._suspect_label.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-weight: {tokens.FONT_WEIGHT_MEDIUM};")
            bot_row.addWidget(self._suspect_label, 1)

        bot_row.addStretch()

        if item.error_count > 0:
            err_pill = StatusPillWidget(status="error", parent=self)
            bot_row.addWidget(err_pill)
        if item.warning_count > 0:
            warn_pill = StatusPillWidget(status="warning", parent=self)
            bot_row.addWidget(warn_pill)

        layout.addLayout(bot_row)

        border_color = tokens.ACCENT if is_selected else tokens.BORDER_HAIRLINE
        bg_color = tokens.SURFACE_2 if is_selected else tokens.SURFACE_1
        self.setStyleSheet(f"""
            QFrame[card="true"] {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: {tokens.RADIUS_MD}px;
            }}
            QFrame[card="true"]:hover {{
                border-color: {tokens.ACCENT};
            }}
        """)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self._on_select(self.item.id)


class CrashesPage(QWidget):
    """Crash logs management and analysis inspection page."""

    import_finished = Signal(int, bool)  # crash_id, was_duplicate
    import_failed = Signal(str, str)     # code, message

    def __init__(
        self,
        service: ProfileService,
        task_manager: TaskManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._task_manager = task_manager or TaskManager(service)

        self._scope: str = "active"  # "active" | "all"
        self._active_profile_name: str | None = None
        self._items: list[CrashListItem] = []
        self._selected_id: int | None = None
        self._is_importing: bool = False

        # Custom callbacks (can be mocked/replaced in tests)
        self.pick_file: Callable[[], str | None] = self._default_pick_file
        self.confirm_delete: Callable[[CrashListItem], bool] = self._default_confirm_delete

        self._init_ui()

    def _default_pick_file(self) -> str | None:
        res, _ = QFileDialog.getOpenFileName(
            self,
            strings.CRASH_IMPORT_DIALOG_TITLE,
            "",
            "Logs & Crash Reports (*.log *.txt *.crash);;All Files (*.*)",
        )
        return res if res else None

    def _default_confirm_delete(self, item: CrashListItem) -> bool:
        dlg = ConfirmDialog(
            title=strings.CRASH_DELETE_CONFIRM_TITLE,
            message=strings.CRASH_DELETE_CONFIRM_MSG.format(name=item.source_name),
            confirm_label="Delete",
            is_destructive=True,
            parent=self,
        )
        return dlg.exec() == ConfirmDialog.DialogCode.Accepted

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        root_layout.setSpacing(tokens.SPACE_MD)

        # Header bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(tokens.SPACE_MD)

        title_col = QVBoxLayout()
        title_col.setSpacing(tokens.SPACE_XS)
        self._title_label = QLabel(strings.CRASH_TITLE, self)
        self._title_label.setProperty("heading", "true")
        t_font = self._title_label.font()
        t_font.setPointSize(tokens.FONT_SIZE_XL)
        t_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(t_font)
        title_col.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.CRASH_SUBTITLE, self)
        self._subtitle_label.setProperty("caption", "true")
        self._subtitle_label.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
        title_col.addWidget(self._subtitle_label)
        header_layout.addLayout(title_col, 1)

        # Controls: Scope filter + Import button
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(tokens.SPACE_SM)

        self._scope_combo = QComboBox(self)
        self._scope_combo.addItem(strings.CRASH_SCOPE_ACTIVE, "active")
        self._scope_combo.addItem(strings.CRASH_SCOPE_ALL, "all")
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        controls_layout.addWidget(self._scope_combo)

        self._import_btn = QPushButton(strings.CRASH_IMPORT_BTN, self)
        self._import_btn.setIcon(icons.get_tinted_icon("add", color_hex=tokens.TEXT_ON_ACCENT, size=16))
        self._import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._import_btn.setProperty("primary", "true")
        self._import_btn.clicked.connect(self._on_import_button_clicked)
        controls_layout.addWidget(self._import_btn)

        header_layout.addLayout(controls_layout)
        root_layout.addLayout(header_layout)

        # Body Stack: 0 = StateCard (empty/loading/error), 1 = Content Split
        self._body_stack = QStackedWidget(self)

        self._state_card = StateCard(self)
        self._body_stack.addWidget(self._state_card)

        # Content Split: Left (Scrollable list of cards) + Right (Scrollable detail panel)
        self._content_split = QWidget(self)
        split_layout = QHBoxLayout(self._content_split)
        split_layout.setContentsMargins(0, 0, 0, 0)
        split_layout.setSpacing(tokens.SPACE_MD)

        # Left list scroll area
        self._list_scroll = QScrollArea(self._content_split)
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFixedWidth(380)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet("background: transparent;")

        self._list_content = QWidget()
        self._list_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._list_layout = QVBoxLayout(self._list_content)
        self._list_layout.setContentsMargins(0, 0, tokens.SPACE_XS, 0)
        self._list_layout.setSpacing(tokens.SPACE_SM)
        self._list_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._list_scroll.setWidget(self._list_content)

        split_layout.addWidget(self._list_scroll)

        # Right detail scroll area
        self._detail_scroll = QScrollArea(self._content_split)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail_scroll.setStyleSheet("background: transparent;")

        self.detail_panel = QWidget()
        self.detail_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._detail_layout = QVBoxLayout(self.detail_panel)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.setSpacing(tokens.SPACE_MD)
        self._detail_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._detail_scroll.setWidget(self.detail_panel)

        split_layout.addWidget(self._detail_scroll, 1)

        self._body_stack.addWidget(self._content_split)
        root_layout.addWidget(self._body_stack, 1)

    # ------------------------------------------------------------------ Scope & Profile
    def set_scope(self, scope: str) -> None:
        """Set crash scope ('active' or 'all')."""
        if scope not in ("active", "all"):
            return
        self._scope = scope
        idx = self._scope_combo.findData(scope)
        if idx >= 0 and self._scope_combo.currentIndex() != idx:
            self._scope_combo.setCurrentIndex(idx)
        self.refresh()

    def scope(self) -> str:
        """Return current crash scope ('active' or 'all')."""
        return self._scope

    def _on_scope_changed(self) -> None:
        scope = self._scope_combo.currentData()
        if scope and scope != self._scope:
            self._scope = scope
            self.refresh()

    def set_active_profile(self, profile_name: str | None) -> None:
        """Update active profile reference and reload data."""
        self._active_profile_name = profile_name
        self.refresh()

    # ------------------------------------------------------------------ Data & Refresh
    def visible_crash_ids(self) -> list[int]:
        """Return list of visible crash IDs in display order (newest first)."""
        return [item.id for item in self._items]

    def selected_crash_id(self) -> int | None:
        """Return currently selected crash ID, or None."""
        return self._selected_id

    def select_crash(self, crash_id: int) -> bool:
        """Select a crash report by ID and populate detail panel."""
        matching = [item for item in self._items if item.id == crash_id]
        if not matching:
            return False

        self._selected_id = crash_id
        self._render_detail_for_id(crash_id)
        self._rebuild_list_cards()
        return True

    def refresh(self) -> None:
        """Reload crashes list from service and update view."""
        try:
            if self._scope == "active":
                self._items = crash_view.list_crashes(self.service, self._active_profile_name)
            else:
                self._items = crash_view.list_crashes(self.service, None)
        except Exception:
            self._items = []

        if not self._items:
            self._selected_id = None
            self._state_card.show_empty(
                title=strings.CRASH_EMPTY_TITLE,
                description="Import Minecraft crash logs or server logs to analyze root causes and conflicting mods.",
                action_text=strings.CRASH_IMPORT_BTN,
                on_action=self._on_import_button_clicked,
                icon_name="crash",
            )
            self._body_stack.setCurrentWidget(self._state_card)
            self._clear_detail()
        else:
            self._body_stack.setCurrentWidget(self._content_split)
            if self._selected_id not in [item.id for item in self._items]:
                self._selected_id = self._items[0].id
            self._render_detail_for_id(self._selected_id)
            self._rebuild_list_cards()

    def _rebuild_list_cards(self) -> None:
        """Rebuild left-column crash card list."""
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        for crash_item in self._items:
            is_selected = crash_item.id == self._selected_id
            card = CrashCard(
                item=crash_item,
                is_selected=is_selected,
                on_select=self.select_crash,
                on_delete=self.request_delete,
                parent=self._list_content,
            )
            self._list_layout.addWidget(card)

        self._list_layout.addStretch()
        self._list_layout.activate()
        self._list_content.adjustSize()

    # ------------------------------------------------------------------ Detail Rendering
    def _clear_detail(self) -> None:
        """Remove all cards from the detail panel."""
        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _render_detail_for_id(self, crash_id: int) -> None:
        """Fetch and render detail view for a specific crash ID."""
        self._clear_detail()
        try:
            detail = crash_view.get_crash_detail(self.service, crash_id)
        except Exception:
            return

        # 1. Notices Card (Always: raw not stored; Conditionally: truncated)
        notices_frame = QFrame(self.detail_panel)
        notices_frame.setObjectName("crashNoticesCard")
        notices_frame.setProperty("card", "true")
        notices_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        notices_layout = QVBoxLayout(notices_frame)
        notices_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        notices_layout.setSpacing(tokens.SPACE_XS)

        if detail.is_truncated:
            trunc_label = BreakAnywhereLabel(strings.CRASH_TRUNCATED_NOTICE, notices_frame)
            trunc_label.setStyleSheet(f"""
                color: {tokens.COLOR_WARNING};
                font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};
                font-size: {tokens.FONT_SIZE_BASE}px;
            """)
            notices_layout.addWidget(trunc_label)

        raw_notice_label = BreakAnywhereLabel(strings.CRASH_RAW_NOT_STORED_NOTICE, notices_frame)
        raw_notice_label.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_SM}px;")
        notices_layout.addWidget(raw_notice_label)

        self._detail_layout.addWidget(notices_frame)

        # 2. Overview Card (Source, MC version, loader, Java, kind, file size, description)
        overview_card = QFrame(self.detail_panel)
        overview_card.setObjectName("crashOverviewCard")
        overview_card.setProperty("card", "true")
        overview_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        ov_layout = QVBoxLayout(overview_card)
        ov_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        ov_layout.setSpacing(tokens.SPACE_SM)

        ov_title = QLabel("Crash Report Overview", overview_card)
        t_font = ov_title.font()
        t_font.setPointSize(tokens.FONT_SIZE_LG)
        t_font.setWeight(QFont.Weight.Bold)
        ov_title.setFont(t_font)
        ov_layout.addWidget(ov_title)

        grid_layout = QVBoxLayout()
        grid_layout.setSpacing(tokens.SPACE_XS)

        src_lbl = BreakAnywhereLabel(f"Source Name: {detail.item.source_name}", overview_card)
        grid_layout.addWidget(src_lbl)

        profile_lbl = BreakAnywhereLabel(f"Profile: {detail.item.display_profile_name}", overview_card)
        grid_layout.addWidget(profile_lbl)

        imp_lbl = BreakAnywhereLabel(f"Imported At: {detail.item.display_imported_at}", overview_card)
        grid_layout.addWidget(imp_lbl)

        kind_lbl = BreakAnywhereLabel(f"Crash Kind: {detail.item.display_kind}", overview_card)
        grid_layout.addWidget(kind_lbl)

        mc_lbl = BreakAnywhereLabel(f"Minecraft Version: {detail.item.mc_version or 'Unknown'}", overview_card)
        grid_layout.addWidget(mc_lbl)

        loader_lbl = BreakAnywhereLabel(f"Loader: {detail.item.loader or 'Unknown'}", overview_card)
        grid_layout.addWidget(loader_lbl)

        java_lbl = BreakAnywhereLabel(f"Java Version: {detail.java_version or 'Unknown'}", overview_card)
        grid_layout.addWidget(java_lbl)

        size_lbl = BreakAnywhereLabel(f"File Size: {detail.item.file_size:,} bytes", overview_card)
        grid_layout.addWidget(size_lbl)

        if detail.description:
            desc_lbl = BreakAnywhereLabel(f"Description: {detail.description}", overview_card)
            grid_layout.addWidget(desc_lbl)

        ov_layout.addLayout(grid_layout)
        self._detail_layout.addWidget(overview_card)

        # 3. Root Cause Card
        rc_card = QFrame(self.detail_panel)
        rc_card.setObjectName("crashRootCauseCard")
        rc_card.setProperty("card", "true")
        rc_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        rc_layout = QVBoxLayout(rc_card)
        rc_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        rc_layout.setSpacing(tokens.SPACE_SM)

        rc_title = QLabel("Root Cause", rc_card)
        r_font = rc_title.font()
        r_font.setPointSize(tokens.FONT_SIZE_BASE)
        r_font.setWeight(QFont.Weight.Bold)
        rc_title.setFont(r_font)
        rc_layout.addWidget(rc_title)

        if detail.root_cause_class:
            class_lbl = BreakAnywhereLabel(f"Root Cause Class: {detail.root_cause_class}", rc_card)
            class_lbl.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};")
            rc_layout.addWidget(class_lbl)

            if detail.root_cause_message:
                msg_lbl = BreakAnywhereLabel(f"Message: {detail.root_cause_message}", rc_card)
                rc_layout.addWidget(msg_lbl)
        else:
            no_rc_lbl = BreakAnywhereLabel("No root cause exception identified", rc_card)
            no_rc_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            rc_layout.addWidget(no_rc_lbl)

        self._detail_layout.addWidget(rc_card)

        # 4. Suspects Card (crashSuspectRow for each suspect, or strings.CRASH_NO_SUSPECTS)
        suspects_card = QFrame(self.detail_panel)
        suspects_card.setObjectName("crashSuspectsCard")
        suspects_card.setProperty("card", "true")
        suspects_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        s_layout = QVBoxLayout(suspects_card)
        s_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        s_layout.setSpacing(tokens.SPACE_MD)

        s_title = QLabel("Identified Suspects", suspects_card)
        s_font = s_title.font()
        s_font.setPointSize(tokens.FONT_SIZE_BASE)
        s_font.setWeight(QFont.Weight.Bold)
        s_title.setFont(s_font)
        s_layout.addWidget(s_title)

        if not detail.suspects:
            no_suspects_lbl = BreakAnywhereLabel(strings.CRASH_NO_SUSPECTS, suspects_card)
            no_suspects_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            s_layout.addWidget(no_suspects_lbl)
        else:
            for suspect in detail.suspects:
                row_widget = QWidget(suspects_card)
                row_widget.setObjectName("crashSuspectRow")
                row_layout = QVBoxLayout(row_widget)
                row_layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
                row_layout.setSpacing(tokens.SPACE_XS)

                top_h = QHBoxLayout()
                top_h.setSpacing(tokens.SPACE_SM)

                badge = CertaintyBadge(suspect.certainty, parent=row_widget)
                top_h.addWidget(badge)

                name_text = f"{suspect.name} ({suspect.mod_id})" if suspect.name != suspect.mod_id else suspect.mod_id
                mod_lbl = BreakAnywhereLabel(name_text, row_widget)
                m_font = mod_lbl.font()
                m_font.setWeight(QFont.Weight.Bold)
                mod_lbl.setFont(m_font)
                top_h.addWidget(mod_lbl, 1)

                attr_lbl = QLabel(f"[{suspect.attribution}]", row_widget)
                attr_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
                top_h.addWidget(attr_lbl)
                top_h.addStretch()
                row_layout.addLayout(top_h)

                reason_lbl = BreakAnywhereLabel(suspect.reason, row_widget)
                row_layout.addWidget(reason_lbl)

                if suspect.evidence_lines:
                    for line_no, line_text in suspect.evidence_lines:
                        ev_lbl = BreakAnywhereLabel(f"Line {line_no}: {line_text}", row_widget)
                        ev_lbl.setStyleSheet(f"""
                            color: {tokens.TEXT_SECONDARY};
                            font-family: Consolas, monospace;
                            font-size: {tokens.FONT_SIZE_XS}px;
                        """)
                        row_layout.addWidget(ev_lbl)

                s_layout.addWidget(row_widget)

        self._detail_layout.addWidget(suspects_card)

        # 5. Findings Card (crashFindingRow for each finding)
        findings_card = QFrame(self.detail_panel)
        findings_card.setObjectName("crashFindingsCard")
        findings_card.setProperty("card", "true")
        findings_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        f_layout = QVBoxLayout(findings_card)
        f_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        f_layout.setSpacing(tokens.SPACE_MD)

        f_title = QLabel("Diagnostic Findings", findings_card)
        f_font = f_title.font()
        f_font.setPointSize(tokens.FONT_SIZE_BASE)
        f_font.setWeight(QFont.Weight.Bold)
        f_title.setFont(f_font)
        f_layout.addWidget(f_title)

        if not detail.findings:
            no_findings_lbl = BreakAnywhereLabel("No findings reported", findings_card)
            no_findings_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            f_layout.addWidget(no_findings_lbl)
        else:
            for finding in detail.findings:
                row_widget = QWidget(findings_card)
                row_widget.setObjectName("crashFindingRow")
                row_layout = QVBoxLayout(row_widget)
                row_layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
                row_layout.setSpacing(tokens.SPACE_XS)

                top_h = QHBoxLayout()
                top_h.setSpacing(tokens.SPACE_SM)

                badge = CertaintyBadge(finding.certainty, parent=row_widget)
                top_h.addWidget(badge)

                sev_pill = StatusPillWidget(status=finding.severity, parent=row_widget)
                top_h.addWidget(sev_pill)

                code_lbl = BreakAnywhereLabel(finding.code, row_widget)
                c_font = code_lbl.font()
                c_font.setWeight(QFont.Weight.Bold)
                code_lbl.setFont(c_font)
                top_h.addWidget(code_lbl, 1)
                top_h.addStretch()
                row_layout.addLayout(top_h)

                msg_lbl = BreakAnywhereLabel(finding.message, row_widget)
                row_layout.addWidget(msg_lbl)

                if finding.suggestion:
                    sugg_lbl = BreakAnywhereLabel(f"Suggestion: {finding.suggestion}", row_widget)
                    sugg_lbl.setStyleSheet(f"color: {tokens.ACCENT};")
                    row_layout.addWidget(sugg_lbl)

                f_layout.addWidget(row_widget)

        self._detail_layout.addWidget(findings_card)

        self._detail_layout.activate()
        self.detail_panel.adjustSize()

    # ------------------------------------------------------------------ Delete Action
    def request_delete(self, crash_id: int) -> None:
        """Prompt confirmation and delete crash report if confirmed."""
        target_item = next((item for item in self._items if item.id == crash_id), None)
        if target_item is None:
            try:
                det = crash_view.get_crash_detail(self.service, crash_id)
                target_item = det.item
            except Exception:
                return

        if self.confirm_delete(target_item):
            self.service.delete_crash(crash_id)
            if self._selected_id == crash_id:
                self._selected_id = None
            self.refresh()

    # ------------------------------------------------------------------ Import Action
    def is_importing(self) -> bool:
        """Return True if background import task is running."""
        return self._is_importing

    def _on_import_button_clicked(self) -> None:
        path = self.pick_file()
        if path:
            self.import_file(path)

    def import_file(self, path: str) -> None:
        """Start asynchronous crash log import."""
        if self.is_importing():
            return

        self._is_importing = True
        self._task_manager.start_import_crash(
            path=path,
            profile_name=self._active_profile_name,
            on_finished=self._on_import_worker_finished,
            on_failed=self._on_import_worker_failed,
        )

    def _on_import_worker_finished(self, crash_id: int, was_duplicate: bool) -> None:
        self._is_importing = False
        self.refresh()
        self.select_crash(crash_id)
        self.import_finished.emit(crash_id, was_duplicate)

    def _on_import_worker_failed(self, code: str, message: str) -> None:
        self._is_importing = False
        self.import_failed.emit(code, message)
