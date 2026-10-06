"""Compare page for inspecting structural deltas and diagnostic diffs between scans."""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QLayout,
)

from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.profiles import FindingDiff, ModDiff, ProfileService, ScanDiff
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.state_card import PageState, StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget


def _format_scan_choice(s: ScanSummary) -> str:
    """Format scan item for picker: #id - Date Time [Label] (icons convey pin)."""
    date_part = s.scanned_at.split("T")[0] if "T" in s.scanned_at else s.scanned_at[:10]
    time_part = s.scanned_at.split("T")[1][:5] if "T" in s.scanned_at else ""
    label_part = f" [{s.label}]" if s.label else ""
    return f"#{s.id} - {date_part} {time_part}{label_part}"


class ElidedComboBox(QComboBox):
    """QComboBox that elides text in the middle when constrained."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.currentIndexChanged.connect(self._update_tooltip)

    def _update_tooltip(self) -> None:
        self.setToolTip(self.currentText())

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        fm = self.fontMetrics()
        avail = max(10, self.width() - 36)
        opt.currentText = fm.elidedText(self.currentText(), Qt.TextElideMode.ElideMiddle, avail)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)


class CollapsibleSection(QFrame):
    """Collapsible section container with header count badge and toggle arrow."""

    def __init__(self, title: str, count: int = 0, is_open: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        self.setProperty("card", "true")
        self._is_open = is_open

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        self._layout.setSpacing(tokens.SPACE_SM)

        # Header bar
        self._header = QWidget(self)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(tokens.SPACE_SM)

        self._toggle_icon = QLabel(self)
        self._toggle_icon.setPixmap(icons.get_tinted_pixmap("chevron_down", color_hex=tokens.TEXT_SECONDARY, size=16))
        h_layout.addWidget(self._toggle_icon)

        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setProperty("section_header", "true")
        h_layout.addWidget(self._title_lbl)

        self._count_badge = QLabel(f"({count})")
        self._count_badge.setProperty("caption", "true")
        self._count_badge.setStyleSheet(f"color: {tokens.ACCENT}; font-weight: {tokens.FONT_WEIGHT_BOLD};")
        h_layout.addWidget(self._count_badge)

        h_layout.addStretch()
        self._layout.addWidget(self._header)

        # Body
        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(tokens.SPACE_XS, tokens.SPACE_XS, tokens.SPACE_XS, tokens.SPACE_XS)
        self._body_layout.setSpacing(tokens.SPACE_SM)
        self._layout.addWidget(self._body)

        self._header.mousePressEvent = lambda e: self.toggle()
        self.set_open(is_open)

    def set_open(self, is_open: bool) -> None:
        self._is_open = is_open
        self._body.setVisible(is_open)
        icon_name = "chevron_down" if is_open else "clear"
        self._toggle_icon.setPixmap(icons.get_tinted_pixmap(icon_name, color_hex=tokens.TEXT_SECONDARY, size=16))

    def toggle(self) -> None:
        self.set_open(not self._is_open)

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def count(self) -> int:
        return self._body_layout.count()


class SummaryChip(QFrame):
    """Summary metric indicator badge with zero-count muting."""

    def __init__(self, prefix: str, count: int, sign: str = "", kind: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        color_map = {
            "success": tokens.COLOR_SUCCESS,
            "error": tokens.COLOR_ERROR,
            "warning": tokens.COLOR_WARNING,
            "info": tokens.COLOR_INFO,
            "neutral": tokens.TEXT_SECONDARY,
        }

        is_zero = (count == 0)
        if is_zero:
            color = tokens.TEXT_MUTED
            bg = tokens.SURFACE_1
            border = tokens.BORDER_HAIRLINE
            text = f"{prefix} 0"
        else:
            color = color_map.get(kind, tokens.TEXT_SECONDARY)
            bg = tokens.SURFACE_2
            border = tokens.BORDER_HAIRLINE
            sign_str = f"{sign}" if sign else ""
            text = f"{prefix} {sign_str}{count}"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        text_lbl = QLabel(text, self)
        weight = tokens.FONT_WEIGHT_MEDIUM if is_zero else tokens.FONT_WEIGHT_SEMIBOLD
        text_lbl.setStyleSheet(f"color: {color}; font-weight: {weight}; font-size: {tokens.FONT_SIZE_SM}px;")
        layout.addWidget(text_lbl)

        self.setStyleSheet(f"""
            SummaryChip {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: {tokens.RADIUS_MD}px;
            }}
        """)


class ComparePage(QWidget):
    """Scan comparison page displaying diffs between two snapshots."""

    scan_requested = Signal()
    scans_updated = Signal()

    def __init__(self, service: ProfileService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._active_profile_name: str | None = None
        self._scans: list[ScanSummary] = []
        self._scan_a_id: int | None = None
        self._scan_b_id: int | None = None

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._main_layout.setSpacing(tokens.SPACE_MD)

        # Header Title and Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(tokens.SPACE_XXS)

        title_lbl = QLabel(strings.COMPARE_TITLE)
        title_lbl.setProperty("heading", "true")
        title_font = title_lbl.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        title_lbl.setFont(title_font)
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel(strings.COMPARE_SUBTITLE)
        sub_lbl.setProperty("subheading", "true")
        header_layout.addWidget(sub_lbl)

        self._main_layout.addLayout(header_layout)

        # Main Controls & Content Container (visible in READY state)
        self._ready_container = QWidget(self)
        ready_layout = QVBoxLayout(self._ready_container)
        ready_layout.setContentsMargins(0, 0, 0, 0)
        ready_layout.setSpacing(tokens.SPACE_MD)

        # 1. Scan Pickers Bar
        picker_frame = QFrame(self)
        picker_frame.setProperty("card", "true")
        picker_layout = QHBoxLayout(picker_frame)
        picker_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        picker_layout.setSpacing(tokens.SPACE_MD)

        # Scan A Picker
        col_a = QVBoxLayout()
        col_a.setSpacing(tokens.SPACE_XXS)
        lbl_a = QLabel(strings.COMPARE_PICKER_A)
        lbl_a.setProperty("section_header", "true")
        col_a.addWidget(lbl_a)

        self._combo_a = ElidedComboBox(self)
        self._combo_a.currentIndexChanged.connect(self._on_picker_changed)
        col_a.addWidget(self._combo_a)
        picker_layout.addLayout(col_a, 3)

        # Scan B Picker
        col_b = QVBoxLayout()
        col_b.setSpacing(tokens.SPACE_XXS)
        lbl_b = QLabel(strings.COMPARE_PICKER_B)
        lbl_b.setProperty("section_header", "true")
        col_b.addWidget(lbl_b)

        self._combo_b = ElidedComboBox(self)
        self._combo_b.currentIndexChanged.connect(self._on_picker_changed)
        col_b.addWidget(self._combo_b)
        picker_layout.addLayout(col_b, 3)

        # Inline Scan B Controls (Label edit + Pin toggle)
        col_actions = QVBoxLayout()
        col_actions.setSpacing(tokens.SPACE_XXS)
        lbl_act = QLabel("TARGET SCAN ACTIONS")
        lbl_act.setProperty("section_header", "true")
        col_actions.addWidget(lbl_act)

        act_row = QHBoxLayout()
        act_row.setContentsMargins(0, 0, 0, 0)
        act_row.setSpacing(tokens.SPACE_XS)

        self._label_input = QLineEdit(self)
        self._label_input.setPlaceholderText(strings.COMPARE_LABEL_PLACEHOLDER)
        self._label_input.returnPressed.connect(self._save_scan_label)
        self._label_input.setMinimumWidth(100)
        act_row.addWidget(self._label_input, 1)

        self._save_label_btn = QPushButton(strings.COMPARE_EDIT_LABEL_BTN, self)
        self._save_label_btn.clicked.connect(self._save_scan_label)
        act_row.addWidget(self._save_label_btn)

        self._pin_btn = QPushButton(strings.COMPARE_PIN_BTN, self)
        self._pin_btn.setIcon(icons.get_tinted_icon("pin", color_hex=tokens.TEXT_SECONDARY, size=14))
        self._pin_btn.clicked.connect(self._toggle_scan_pin)
        act_row.addWidget(self._pin_btn)

        col_actions.addLayout(act_row)
        picker_layout.addLayout(col_actions, 4)

        ready_layout.addWidget(picker_frame)

        # 2. Same-Scan Hint Banner
        self._same_scan_banner = QFrame(self)
        self._same_scan_banner.setProperty("card_elevated", "true")
        ss_layout = QHBoxLayout(self._same_scan_banner)
        ss_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        ss_layout.setSpacing(tokens.SPACE_SM)
        ss_icon = QLabel()
        ss_icon.setPixmap(icons.get_tinted_pixmap("info", color_hex=tokens.ACCENT, size=18))
        ss_layout.addWidget(ss_icon)
        ss_text = QLabel(strings.COMPARE_SAME_SCAN_HINT)
        ss_text.setProperty("subheading", "true")
        ss_layout.addWidget(ss_text, 1)
        ready_layout.addWidget(self._same_scan_banner)
        self._same_scan_banner.setVisible(False)

        # 3. Summary Chips Bar
        self._chips_bar = QWidget(self)
        self._chips_layout = QHBoxLayout(self._chips_bar)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(tokens.SPACE_SM)
        ready_layout.addWidget(self._chips_bar)

        # 4. Scrollable Diff Sections Area
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._scroll_layout.setContentsMargins(0, 0, tokens.SPACE_SM, 0)
        self._scroll_layout.setSpacing(tokens.SPACE_MD)
        self._scroll_layout.addStretch()

        self._scroll_area.setWidget(self._scroll_content)
        ready_layout.addWidget(self._scroll_area, 1)

        self._main_layout.addWidget(self._ready_container, 1)

        # State Card (Empty / Loading / Fewer than 2 scans)
        self._state_card = StateCard(self)
        self._main_layout.addWidget(self._state_card)

        self.set_state(PageState.EMPTY)

    def set_state(self, state: PageState) -> None:
        if state == PageState.READY:
            self._state_card.setVisible(False)
            self._ready_container.setVisible(True)
        else:
            self._ready_container.setVisible(False)
            self._state_card.setVisible(True)

    def set_profile(self, name: str | None) -> None:
        self._active_profile_name = name
        if not name:
            self.set_state(PageState.EMPTY)
            self._state_card.show_empty(
                title=strings.STATE_EMPTY_TITLE,
                description=strings.DASHBOARD_EMPTY_DESC,
                icon_name="folder",
            )
            return

        try:
            profile = self.service.get_profile(name)
            self._scans = self.service.get_history(name, limit=50)

            if len(self._scans) < 2:
                self.set_state(PageState.EMPTY)
                self._state_card.show_empty(
                    title=strings.COMPARE_FEWER_THAN_TWO_TITLE,
                    description=strings.COMPARE_FEWER_THAN_TWO_DESC,
                    action_text=strings.STATE_ACTION_SCAN_NOW,
                    on_action=self.scan_requested.emit,
                    icon_name="scan",
                )
                return

            self.set_state(PageState.READY)
            self._populate_pickers()
            self._run_diff()

        except Exception as exc:
            self.set_state(PageState.ERROR)
            self._state_card.show_error(
                title=strings.STATE_ERROR_TITLE,
                description=str(exc),
                code=type(exc).__name__.upper(),
                action_text=strings.STATE_ACTION_RETRY,
                on_action=self.scan_requested.emit,
            )

    def _populate_pickers(self) -> None:
        self._combo_a.blockSignals(True)
        self._combo_b.blockSignals(True)

        self._combo_a.clear()
        self._combo_b.clear()

        for s in self._scans:
            text = _format_scan_choice(s)
            icon = icons.get_tinted_icon("pin", color_hex=tokens.ACCENT, size=14) if s.pinned else icons.get_tinted_icon("scan", color_hex=tokens.TEXT_MUTED, size=14)
            self._combo_a.addItem(icon, text, userData=s.id)
            self._combo_b.addItem(icon, text, userData=s.id)

        # Default: scan A = previous (index 1), scan B = latest (index 0)
        self._combo_a.setCurrentIndex(1)
        self._combo_b.setCurrentIndex(0)

        self._combo_a.blockSignals(False)
        self._combo_b.blockSignals(False)
        self._update_target_scan_controls()

    def _update_target_scan_controls(self) -> None:
        scan_b_id = self._combo_b.currentData()
        scan_b = next((s for s in self._scans if s.id == scan_b_id), None)
        if scan_b:
            self._label_input.setText(scan_b.label or "")
            self._pin_btn.setText(strings.COMPARE_UNPIN_BTN if scan_b.pinned else strings.COMPARE_PIN_BTN)
            self._pin_btn.setIcon(icons.get_tinted_icon("pin", color_hex=tokens.ACCENT if scan_b.pinned else tokens.TEXT_SECONDARY, size=14))

    def _save_scan_label(self) -> None:
        if not self._active_profile_name:
            return
        scan_b_id = self._combo_b.currentData()
        if not scan_b_id:
            return
        new_label = self._label_input.text().strip() or None
        self.service.set_scan_label(self._active_profile_name, scan_b_id, new_label)
        self.set_profile(self._active_profile_name)
        self.scans_updated.emit()

    def _toggle_scan_pin(self) -> None:
        if not self._active_profile_name:
            return
        scan_b_id = self._combo_b.currentData()
        if not scan_b_id:
            return
        scan_b = next((s for s in self._scans if s.id == scan_b_id), None)
        if scan_b:
            self.service.set_scan_pinned(self._active_profile_name, scan_b_id, not scan_b.pinned)
            self.set_profile(self._active_profile_name)
            self.scans_updated.emit()

    def _on_picker_changed(self) -> None:
        self._update_target_scan_controls()
        self._run_diff()

    def _clear_diff_layout(self) -> None:
        while self._scroll_layout.count() > 1:
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().setParent(None)
                        sub.widget().deleteLater()

        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item.widget():
                w = item.widget()
                w.setParent(None)
                w.deleteLater()

    def _run_diff(self) -> None:
        if not self._active_profile_name:
            return

        id_a = self._combo_a.currentData()
        id_b = self._combo_b.currentData()

        if id_a is None or id_b is None:
            return

        self._clear_diff_layout()

        if id_a == id_b:
            self._same_scan_banner.setVisible(True)
            return
        else:
            self._same_scan_banner.setVisible(False)

        diff: ScanDiff = self.service.diff_scans(self._active_profile_name, id_a, id_b)

        # 1. Summary Chips
        added_mods = [m for m in diff.top_level_mods if m.change_type == "added"]
        removed_mods = [m for m in diff.top_level_mods if m.change_type == "removed"]
        changed_mods = [m for m in diff.top_level_mods if m.change_type == "changed"]

        chip_add = SummaryChip("Added", len(added_mods), sign="+", kind="success", parent=self)
        chip_rem = SummaryChip("Removed", len(removed_mods), sign="-", kind="error", parent=self)
        chip_chg = SummaryChip("Changed", len(changed_mods), sign="", kind="warning", parent=self)
        chip_res = SummaryChip("Resolved", len(diff.findings_resolved), sign="", kind="success", parent=self)
        chip_new = SummaryChip("New", len(diff.findings_added), sign="+", kind="error", parent=self)

        self._chips_layout.addWidget(chip_add)
        self._chips_layout.addWidget(chip_rem)
        self._chips_layout.addWidget(chip_chg)
        self._chips_layout.addWidget(chip_res)
        self._chips_layout.addWidget(chip_new)
        self._chips_layout.addStretch()

        insert_idx = 0

        # 2. Loader / MC Diff Section
        if diff.loader_changed or diff.mc_version_changed:
            sec_env = CollapsibleSection(strings.COMPARE_SEC_LOADER_MC, count=1, is_open=True, parent=self._scroll_content)
            if diff.loader_changed:
                old_l = diff.loader_changed.get("old") or "None"
                new_l = diff.loader_changed.get("new") or "None"
                l_lbl = QLabel(f"<b>Mod Loader Changed:</b> {old_l} &rarr; <b>{new_l}</b>")
                l_lbl.setProperty("subheading", "true")
                sec_env.add_widget(l_lbl)
            if diff.mc_version_changed:
                old_mc = diff.mc_version_changed.get("old") or "None"
                new_mc = diff.mc_version_changed.get("new") or "None"
                mc_lbl = QLabel(f"<b>Minecraft Version Changed:</b> {old_mc} &rarr; <b>{new_mc}</b>")
                mc_lbl.setProperty("subheading", "true")
                sec_env.add_widget(mc_lbl)

            self._scroll_layout.insertWidget(insert_idx, sec_env)
            insert_idx += 1

        # 3. Added Mods Section (collapses when empty)
        if added_mods:
            sec_add = CollapsibleSection(strings.COMPARE_SEC_ADDED_MODS, count=len(added_mods), is_open=True, parent=self._scroll_content)
            for m in added_mods:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS)
                pill = StatusPillWidget("added", self)
                rl.addWidget(pill)
                id_lbl = QLabel(f"<b>{m.id}</b> v{m.new_version or 'Unknown'}")
                id_lbl.setProperty("subheading", "true")
                rl.addWidget(id_lbl)
                rl.addStretch()
                if m.new_jar_path:
                    j_lbl = QLabel(m.new_jar_path.split("/")[-1].split("\\")[-1])
                    j_lbl.setProperty("caption", "true")
                    rl.addWidget(j_lbl)
                sec_add.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_add)
            insert_idx += 1

        # 4. Removed Mods Section
        if removed_mods:
            sec_rem = CollapsibleSection(strings.COMPARE_SEC_REMOVED_MODS, count=len(removed_mods), is_open=True, parent=self._scroll_content)
            for m in removed_mods:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS)
                pill = StatusPillWidget("removed", self)
                rl.addWidget(pill)
                id_lbl = QLabel(f"<b>{m.id}</b> (was v{m.old_version or 'Unknown'})")
                id_lbl.setProperty("subheading", "true")
                rl.addWidget(id_lbl)
                rl.addStretch()
                sec_rem.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_rem)
            insert_idx += 1

        # 5. Changed Mods Section (shows old -> new version, jar, state)
        if changed_mods:
            sec_chg = CollapsibleSection(strings.COMPARE_SEC_CHANGED_MODS, count=len(changed_mods), is_open=True, parent=self._scroll_content)
            for m in changed_mods:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QVBoxLayout(row)
                rl.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
                rl.setSpacing(tokens.SPACE_XXS)

                top_h = QHBoxLayout()
                m_title = QLabel(f"<b>{m.id}</b>")
                m_title.setProperty("subheading", "true")
                top_h.addWidget(m_title)
                top_h.addStretch()

                if not m.old_disabled and m.new_disabled:
                    chg_kind = "disabled"
                elif m.old_disabled and not m.new_disabled:
                    chg_kind = "enabled"
                else:
                    chg_kind = "changed"
                chg_pill = StatusPillWidget(chg_kind, self)
                top_h.addWidget(chg_pill)
                rl.addLayout(top_h)

                if m.old_version != m.new_version:
                    v_diff = QLabel(f"Version: {m.old_version or 'None'} &rarr; <b>{m.new_version or 'None'}</b>")
                    v_diff.setProperty("caption", "true")
                    rl.addWidget(v_diff)

                if m.old_jar_path != m.new_jar_path:
                    old_j = (m.old_jar_path or "").split("/")[-1].split("\\")[-1]
                    new_j = (m.new_jar_path or "").split("/")[-1].split("\\")[-1]
                    j_diff = QLabel(f"Jar File: {old_j} &rarr; <b>{new_j}</b>")
                    j_diff.setProperty("caption", "true")
                    rl.addWidget(j_diff)

                if m.old_disabled != m.new_disabled:
                    st_old = "Disabled" if m.old_disabled else "Active"
                    st_new = "Disabled" if m.new_disabled else "Active"
                    s_diff = QLabel(f"State: {st_old} &rarr; <b>{st_new}</b>")
                    s_diff.setProperty("caption", "true")
                    rl.addWidget(s_diff)

                sec_chg.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_chg)
            insert_idx += 1

        # 6. New Diagnostic Findings Section
        if diff.findings_added:
            sec_f_add = CollapsibleSection(strings.COMPARE_SEC_FINDINGS_ADDED, count=len(diff.findings_added), is_open=True, parent=self._scroll_content)
            for f in diff.findings_added:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QVBoxLayout(row)
                rl.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS)
                hdr = QHBoxLayout()
                pill = StatusPillWidget(f.severity, self)
                hdr.addWidget(pill)
                code_l = QLabel(f.code)
                code_l.setProperty("code", "true")
                hdr.addWidget(code_l)
                hdr.addStretch()
                rl.addLayout(hdr)
                msg_l = QLabel(f.message)
                msg_l.setProperty("subheading", "true")
                msg_l.setWordWrap(True)
                rl.addWidget(msg_l)
                sec_f_add.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_f_add)
            insert_idx += 1

        # 7. Resolved Findings Section
        if diff.findings_resolved:
            sec_f_res = CollapsibleSection(strings.COMPARE_SEC_FINDINGS_RESOLVED, count=len(diff.findings_resolved), is_open=True, parent=self._scroll_content)
            for f in diff.findings_resolved:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QVBoxLayout(row)
                rl.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_XS, tokens.SPACE_MD, tokens.SPACE_XS)
                hdr = QHBoxLayout()
                pill = StatusPillWidget("ok", self)
                hdr.addWidget(pill)
                code_l = QLabel(f"Resolved: {f.code}")
                code_l.setProperty("code", "true")
                hdr.addWidget(code_l)
                hdr.addStretch()
                rl.addLayout(hdr)
                msg_l = QLabel(f.message)
                msg_l.setProperty("subheading", "true")
                msg_l.setWordWrap(True)
                rl.addWidget(msg_l)
                sec_f_res.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_f_res)
            insert_idx += 1

        # 8. Nested Mods in a separate collapsed section
        if diff.nested_mods:
            sec_nest = CollapsibleSection(strings.COMPARE_SEC_NESTED_MODS, count=len(diff.nested_mods), is_open=False, parent=self._scroll_content)
            for m in diff.nested_mods:
                row = QFrame()
                row.setProperty("card_elevated", "true")
                rl = QHBoxLayout(row)
                pill_kind = m.change_type if m.change_type in ("added", "removed", "changed") else "info"
                pill = StatusPillWidget(pill_kind, self)
                rl.addWidget(pill)
                id_lbl = QLabel(f"<b>{m.id}</b> ({m.change_type}): v{m.old_version or '?'} &rarr; v{m.new_version or '?'}")
                id_lbl.setProperty("subheading", "true")
                rl.addWidget(id_lbl)
                rl.addStretch()
                sec_nest.add_widget(row)
            self._scroll_layout.insertWidget(insert_idx, sec_nest)
            insert_idx += 1
