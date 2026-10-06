"""Reports page for in-depth diagnostic report inspection and HTML/JSON export."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPainter, QTextLayout, QTextOption
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

from mmis.domain.models import Severity
from mmis.domain.profiles import ScanRecord
from mmis.services.export import export_report_html, export_report_json
from mmis.services.profiles import ProfileService
from mmis.services.report import (
    DiagnosticReport,
    Recommendation,
    ReportFinding,
    build_diagnostic_report,
    format_certainty,
)
from mmis.ui import strings
from mmis.ui.pages.issues import CertaintyBadge
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.confirm_dialog import ConfirmDialog
from mmis.ui.widgets.state_card import StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget
from mmis.ui.workers import TaskManager


def _norm_key(s: str) -> str:
    """Normalize metric key to lowercase underscore identifier."""
    return str(s).strip().lower().replace(" ", "_")


class ReportsPage(QWidget):
    """Reports page presenting full diagnostic reports with native widgets and export."""

    export_finished = Signal(str)       # exported path
    export_failed = Signal(str, str)    # code, message

    def __init__(
        self,
        service: ProfileService,
        task_manager: TaskManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._task_manager = task_manager or TaskManager(service)

        self._active_profile_name: str | None = None
        self._scan_history: list[ScanRecord] = []
        self._selected_scan_id: int | None = None
        self._current_report: DiagnosticReport | None = None
        self._is_loading: bool = False

        # Replaceable callbacks for testing and automation
        self.confirm_overwrite: Callable[[str], bool] = self._default_confirm_overwrite
        self.pick_save_path: Callable[[str], str | None] = self._default_pick_save_path
        self.open_path: Callable[[str], None] = self._default_open_path

        self._init_ui()

    def _default_confirm_overwrite(self, path: str) -> bool:
        dlg = ConfirmDialog(
            title=strings.REPORTS_OVERWRITE_TITLE,
            message=strings.REPORTS_OVERWRITE_MSG.format(path=path),
            confirm_label="Overwrite",
            is_destructive=True,
            parent=self,
        )
        return dlg.exec() == ConfirmDialog.DialogCode.Accepted

    def _default_pick_save_path(self, fmt: str) -> str | None:
        filt = "HTML Files (*.html)" if fmt.lower() == "html" else "JSON Files (*.json)"
        res, _ = QFileDialog.getSaveFileName(
            self,
            f"Export Report ({fmt.upper()})",
            f"report_{self._active_profile_name or 'modpack'}.{fmt.lower()}",
            filt,
        )
        return res if res else None

    def _default_open_path(self, path: str) -> None:
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        root_layout.setSpacing(tokens.SPACE_MD)

        # Header Bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(tokens.SPACE_MD)

        title_col = QVBoxLayout()
        title_col.setSpacing(tokens.SPACE_XS)
        self._title_label = QLabel(strings.REPORTS_TITLE, self)
        self._title_label.setProperty("heading", "true")
        t_font = self._title_label.font()
        t_font.setPointSize(tokens.FONT_SIZE_XL)
        t_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(t_font)
        title_col.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.REPORTS_SUBTITLE, self)
        self._subtitle_label.setProperty("caption", "true")
        self._subtitle_label.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
        title_col.addWidget(self._subtitle_label)
        header_layout.addLayout(title_col, 1)

        # Controls: Scan selector + Export buttons
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(tokens.SPACE_SM)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._scan_label = QLabel(strings.REPORTS_SCAN_LABEL, self)
        controls_layout.addWidget(self._scan_label)

        self._scan_combo = QComboBox(self)
        self._scan_combo.currentIndexChanged.connect(self._on_scan_combo_changed)
        controls_layout.addWidget(self._scan_combo)

        self._export_html_btn = QPushButton(strings.REPORTS_BTN_EXPORT_HTML, self)
        self._export_html_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_html_btn.setIcon(icons.get_tinted_icon("report", color_hex=tokens.TEXT_PRIMARY, size=16))
        self._export_html_btn.clicked.connect(lambda: self._on_export_button_clicked("html"))
        controls_layout.addWidget(self._export_html_btn)

        self._export_json_btn = QPushButton(strings.REPORTS_BTN_EXPORT_JSON, self)
        self._export_json_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_json_btn.clicked.connect(lambda: self._on_export_button_clicked("json"))
        controls_layout.addWidget(self._export_json_btn)

        header_layout.addLayout(controls_layout)
        root_layout.addLayout(header_layout)

        # Body Stack: 0 = StateCard, 1 = Content ScrollArea
        self._body_stack = QStackedWidget(self)

        self._state_card = StateCard(self)
        self._body_stack.addWidget(self._state_card)

        # Scrollable Report Content
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet("background: transparent;")

        self._content_widget = QWidget()
        self._content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, tokens.SPACE_MD, 0)
        self._content_layout.setSpacing(tokens.SPACE_MD)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll_area.setWidget(self._content_widget)

        self._body_stack.addWidget(self._scroll_area)
        root_layout.addWidget(self._body_stack, 1)

    # ------------------------------------------------------------------ Profile & Scans
    def set_active_profile(self, profile_name: str | None) -> None:
        """Update active profile and reload scan history."""
        self._active_profile_name = profile_name
        self.refresh()

    def scan_ids(self) -> Sequence[int]:
        """Return list of available scan IDs in history order (newest first)."""
        return [s.id for s in self._scan_history]

    def selected_scan_id(self) -> int | None:
        """Return currently selected scan ID."""
        return self._selected_scan_id

    def select_scan(self, scan_id: int) -> None:
        """Select a scan ID and load report."""
        if scan_id == self._selected_scan_id and self._current_report is not None:
            return
        self._selected_scan_id = scan_id
        idx = self._scan_combo.findData(scan_id)
        if idx >= 0 and self._scan_combo.currentIndex() != idx:
            self._scan_combo.blockSignals(True)
            self._scan_combo.setCurrentIndex(idx)
            self._scan_combo.blockSignals(False)
        self._load_report_for_scan(scan_id)

    def current_report(self) -> DiagnosticReport | None:
        """Return the currently rendered DiagnosticReport or None."""
        return self._current_report

    def _on_scan_combo_changed(self) -> None:
        scan_id = self._scan_combo.currentData()
        if scan_id is not None and scan_id != self._selected_scan_id:
            self.select_scan(scan_id)

    def refresh(self) -> None:
        """Reload profile history and rebuild report view."""
        if not self._active_profile_name:
            self._scan_history = []
            self._selected_scan_id = None
            self._current_report = None
            self._show_empty_state()
            return

        try:
            self._scan_history = list(self.service.get_history(self._active_profile_name))
        except Exception:
            self._scan_history = []

        if not self._scan_history:
            self._selected_scan_id = None
            self._current_report = None
            self._show_empty_state()
            return

        # Populate scan combo
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        for s in self._scan_history:
            date_str = s.scanned_at[:16].replace("T", " ")
            lbl = f"Scan #{s.id} ({date_str})"
            if s.label:
                lbl += f" - {s.label}"
            self._scan_combo.addItem(lbl, s.id)
        self._scan_combo.blockSignals(False)

        # Select latest scan by default if current selection is invalid
        history_ids = [s.id for s in self._scan_history]
        if self._selected_scan_id not in history_ids:
            self._selected_scan_id = history_ids[0]

        idx = self._scan_combo.findData(self._selected_scan_id)
        if idx >= 0:
            self._scan_combo.blockSignals(True)
            self._scan_combo.setCurrentIndex(idx)
            self._scan_combo.blockSignals(False)

        self._load_report_for_scan(self._selected_scan_id)

    def _show_empty_state(self) -> None:
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        self._scan_combo.blockSignals(False)
        self._export_html_btn.setEnabled(False)
        self._export_json_btn.setEnabled(False)
        self._state_card.show_empty(
            title=strings.REPORTS_EMPTY_TITLE,
            description=strings.REPORTS_EMPTY_DESC,
            action_text="",
            icon_name="report",
        )
        self._body_stack.setCurrentWidget(self._state_card)

    def _load_report_for_scan(self, scan_id: int) -> None:
        """Fetch and render diagnostic report via worker."""
        if not self._active_profile_name:
            return

        self._is_loading = True
        self._current_report = None
        self._export_html_btn.setEnabled(False)
        self._export_json_btn.setEnabled(False)

        try:
            profile = self.service.get_profile(self._active_profile_name)
            scan_rec = self.service.get_scan_record(self._active_profile_name, scan_id)
        except Exception as exc:
            self._is_loading = False
            self._state_card.show_error(title="Failed to Load Scan", description=str(exc))
            self._body_stack.setCurrentWidget(self._state_card)
            return

        self._task_manager.start_build_report(
            profile_name=profile.name,
            scan_record=scan_rec,
            on_finished=self._on_report_built,
            on_failed=self._on_report_failed,
        )

    def _on_report_built(self, report: DiagnosticReport) -> None:
        self._is_loading = False
        self._current_report = report
        self._export_html_btn.setEnabled(True)
        self._export_json_btn.setEnabled(True)
        self._render_report(report)
        self._body_stack.setCurrentWidget(self._scroll_area)

    def _on_report_failed(self, code: str, msg: str) -> None:
        self._is_loading = False
        self._current_report = None
        self._state_card.show_error(title=f"Report Generation Failed ({code})", description=msg)
        self._body_stack.setCurrentWidget(self._state_card)

    # ------------------------------------------------------------------ Render Preview
    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _render_report(self, report: DiagnosticReport) -> None:
        self._clear_content()

        # 1. Overview Card
        ov_card = QFrame(self._content_widget)
        ov_card.setObjectName("reportOverviewCard")
        ov_card.setProperty("card", "true")
        ov_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        ov_layout = QVBoxLayout(ov_card)
        ov_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        ov_layout.setSpacing(tokens.SPACE_SM)
        ov_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        header_row = QHBoxLayout()
        header_row.setSpacing(tokens.SPACE_MD)

        title_lbl = QLabel(f"Diagnostic Report: {report.profile_name}", ov_card)
        t_font = title_lbl.font()
        t_font.setPointSize(tokens.FONT_SIZE_LG)
        t_font.setWeight(QFont.Weight.Bold)
        title_lbl.setFont(t_font)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_row.addWidget(title_lbl, 1)

        status_lbl = QLabel(f"Status: {report.status}", ov_card)
        status_lbl.setObjectName("reportStatusLabel")
        status_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        status_lbl.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_BOLD}; color: {tokens.COLOR_ERROR if 'fail' in report.status.lower() or 'error' in report.status.lower() else tokens.COLOR_SUCCESS};")
        header_row.addWidget(status_lbl)
        ov_layout.addLayout(header_row)

        meta_grid = QVBoxLayout()
        meta_grid.setSpacing(tokens.SPACE_XS)

        p_lbl = QLabel(f"Path: {report.profile_path}", ov_card)
        p_lbl.setWordWrap(True)
        meta_grid.addWidget(p_lbl)
        date_str = report.scanned_at[:16].replace("T", " ")
        lbl_info = f"Scan ID: {report.scan_id}  |  Scanned At: {date_str} UTC"
        if report.scan_label:
            lbl_info += f"  |  Label: {report.scan_label}"
        info_lbl = QLabel(lbl_info, ov_card)
        info_lbl.setWordWrap(True)
        meta_grid.addWidget(info_lbl)

        env_str = f"Environment: Loader: {report.detected_loader or 'None'}  |  Minecraft: {report.detected_mc_version or 'None'}"
        env_lbl = QLabel(env_str, ov_card)
        env_lbl.setWordWrap(True)
        meta_grid.addWidget(env_lbl)
        ov_layout.addLayout(meta_grid)
        self._content_layout.addWidget(ov_card)

        # 2. Metrics & Counts Card (Severity Counts & Certainty Counts)
        counts_card = QFrame(self._content_widget)
        counts_card.setObjectName("reportCountsCard")
        counts_card.setProperty("card", "true")
        counts_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        c_layout = QVBoxLayout(counts_card)
        c_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        c_layout.setSpacing(tokens.SPACE_MD)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        counts_title = QLabel("Findings Summary & Counts", counts_card)
        ct_font = counts_title.font()
        ct_font.setPointSize(tokens.FONT_SIZE_BASE)
        ct_font.setWeight(QFont.Weight.Bold)
        counts_title.setFont(ct_font)
        c_layout.addWidget(counts_title)

        row_split = QHBoxLayout()
        row_split.setSpacing(tokens.SPACE_LG)

        # Severity counts column
        sev_col = QVBoxLayout()
        sev_col.setSpacing(tokens.SPACE_XS)
        sev_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        sev_hdr = QLabel("By Severity:", counts_card)
        sev_hdr.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_SEMIBOLD}; color: {tokens.TEXT_SECONDARY};")
        sev_col.addWidget(sev_hdr)

        sev_order = ["error", "warning", "info"]
        ordered_sevs = [k for k in sev_order if k in report.severity_counts] + [k for k in sorted(report.severity_counts) if k not in sev_order]
        for s_key in ordered_sevs:
            val = report.severity_counts[s_key]
            line_box = QHBoxLayout()
            line_box.setSpacing(tokens.SPACE_SM)
            lbl_title = QLabel(f"{s_key.capitalize()}:", counts_card)
            line_box.addWidget(lbl_title)

            cnt_lbl = QLabel(str(val), counts_card)
            cnt_lbl.setObjectName(f"reportSeverityCount_{_norm_key(s_key)}")
            cnt_lbl.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_BOLD};")
            line_box.addWidget(cnt_lbl)
            line_box.addStretch()
            sev_col.addLayout(line_box)

        row_split.addLayout(sev_col, 1)

        # Certainty counts column
        cert_col = QVBoxLayout()
        cert_col.setSpacing(tokens.SPACE_XS)
        cert_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        cert_hdr = QLabel("By Certainty:", counts_card)
        cert_hdr.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_SEMIBOLD}; color: {tokens.TEXT_SECONDARY};")
        cert_col.addWidget(cert_hdr)

        cert_order = ["Certain", "Possible", "Insufficient data"]
        ordered_certs = [k for k in cert_order if k in report.certainty_counts] + [k for k in sorted(report.certainty_counts) if k not in cert_order]
        for c_key in ordered_certs:
            val = report.certainty_counts[c_key]
            line_box = QHBoxLayout()
            line_box.setSpacing(tokens.SPACE_SM)
            lbl_title = QLabel(f"{c_key}:", counts_card)
            line_box.addWidget(lbl_title)

            cnt_lbl = QLabel(str(val), counts_card)
            cnt_lbl.setObjectName(f"reportCertaintyCount_{_norm_key(c_key)}")
            cnt_lbl.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_BOLD};")
            line_box.addWidget(cnt_lbl)
            line_box.addStretch()
            cert_col.addLayout(line_box)

        row_split.addLayout(cert_col, 1)
        c_layout.addLayout(row_split)
        self._content_layout.addWidget(counts_card)

        # 3. Recommendations Card
        recs_card = QFrame(self._content_widget)
        recs_card.setObjectName("reportRecommendationsCard")
        recs_card.setProperty("card", "true")
        recs_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        r_layout = QVBoxLayout(recs_card)
        r_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        r_layout.setSpacing(tokens.SPACE_SM)
        r_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        rec_title = QLabel("Recommendations", recs_card)
        rt_font = rec_title.font()
        rt_font.setPointSize(tokens.FONT_SIZE_BASE)
        rt_font.setWeight(QFont.Weight.Bold)
        rec_title.setFont(rt_font)
        r_layout.addWidget(rec_title)

        if not report.recommendations:
            none_lbl = QLabel("No actionable recommendations.", recs_card)
            none_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            r_layout.addWidget(none_lbl)
        else:
            for rec in report.recommendations:
                row_widget = QWidget(recs_card)
                row_widget.setObjectName("reportRecommendationRow")
                rw_layout = QVBoxLayout(row_widget)
                rw_layout.setContentsMargins(0, tokens.SPACE_XS, 0, tokens.SPACE_XS)
                rw_layout.setSpacing(tokens.SPACE_XS)

                top_h = QHBoxLayout()
                top_h.setSpacing(tokens.SPACE_SM)
                sev_pill = StatusPillWidget(status=rec.severity, parent=row_widget)
                top_h.addWidget(sev_pill)

                sugg_lbl = QLabel(rec.suggestion, row_widget)
                sugg_lbl.setWordWrap(True)
                s_font = sugg_lbl.font()
                s_font.setWeight(QFont.Weight.Medium)
                sugg_lbl.setFont(s_font)
                top_h.addWidget(sugg_lbl, 1)
                rw_layout.addLayout(top_h)

                if rec.finding_codes or rec.affected_mod_ids:
                    details = []
                    if rec.finding_codes:
                        details.append(f"Findings: {', '.join(rec.finding_codes)}")
                    if rec.affected_mod_ids:
                        details.append(f"Mods: {', '.join(rec.affected_mod_ids)}")
                    det_lbl = QLabel(" | ".join(details), row_widget)
                    det_lbl.setWordWrap(True)
                    det_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
                    rw_layout.addWidget(det_lbl)

                r_layout.addWidget(row_widget)

        self._content_layout.addWidget(recs_card)

        # 4. Diagnostic Findings by Category
        find_card = QFrame(self._content_widget)
        find_card.setObjectName("reportFindingsCard")
        find_card.setProperty("card", "true")
        find_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        f_layout = QVBoxLayout(find_card)
        f_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        f_layout.setSpacing(tokens.SPACE_MD)
        f_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        f_title = QLabel("Diagnostic Findings by Category", find_card)
        ft_font = f_title.font()
        ft_font.setPointSize(tokens.FONT_SIZE_BASE)
        ft_font.setWeight(QFont.Weight.Bold)
        f_title.setFont(ft_font)
        f_layout.addWidget(f_title)

        has_any_findings = any(bool(flist) for flist in report.findings_by_category.values())
        if not has_any_findings:
            no_f_lbl = QLabel("No findings detected.", find_card)
            no_f_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            f_layout.addWidget(no_f_lbl)
        else:
            for cat_name in sorted(report.findings_by_category):
                flist = report.findings_by_category[cat_name]
                if not flist:
                    continue
                cat_hdr = QLabel(f"{cat_name} ({len(flist)})", find_card)
                cat_hdr.setStyleSheet(f"font-weight: {tokens.FONT_WEIGHT_BOLD}; color: {tokens.ACCENT};")
                f_layout.addWidget(cat_hdr)

                for f in flist:
                    frow = QWidget(find_card)
                    frow_layout = QVBoxLayout(frow)
                    frow_layout.setContentsMargins(tokens.SPACE_SM, tokens.SPACE_XS, 0, tokens.SPACE_XS)
                    frow_layout.setSpacing(tokens.SPACE_XS)
                    frow_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

                    row_h = QHBoxLayout()
                    row_h.setSpacing(tokens.SPACE_SM)
                    badge = CertaintyBadge(f.certainty, parent=frow)
                    row_h.addWidget(badge)
                    pill = StatusPillWidget(status=f.severity, parent=frow)
                    row_h.addWidget(pill)
                    code_lbl = QLabel(f.code, frow)
                    c_font = code_lbl.font()
                    c_font.setWeight(QFont.Weight.Bold)
                    code_lbl.setFont(c_font)
                    row_h.addWidget(code_lbl)
                    row_h.addStretch()
                    frow_layout.addLayout(row_h)

                    msg_lbl = QLabel(f.message, frow)
                    msg_lbl.setWordWrap(True)
                    frow_layout.addWidget(msg_lbl)

                    if f.suggestion:
                        sugg_lbl = QLabel(f"Suggestion: {f.suggestion}", frow)
                        sugg_lbl.setWordWrap(True)
                        sugg_lbl.setStyleSheet(f"color: {tokens.ACCENT};")
                        frow_layout.addWidget(sugg_lbl)

                    f_layout.addWidget(frow)

        self._content_layout.addWidget(find_card)

        # 5. Latest Crash Summary Card
        crash_card = QFrame(self._content_widget)
        crash_card.setObjectName("reportCrashCard")
        crash_card.setProperty("card", "true")
        crash_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        cr_layout = QVBoxLayout(crash_card)
        cr_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        cr_layout.setSpacing(tokens.SPACE_SM)
        cr_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        cr_title = QLabel("Latest Crash Report Analysis", crash_card)
        crt_font = cr_title.font()
        crt_font.setPointSize(tokens.FONT_SIZE_BASE)
        crt_font.setWeight(QFont.Weight.Bold)
        cr_title.setFont(crt_font)
        cr_layout.addWidget(cr_title)

        if report.latest_crash is None:
            no_cr_lbl = QLabel(strings.REPORTS_NO_CRASH, crash_card)
            no_cr_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
            cr_layout.addWidget(no_cr_lbl)
        else:
            c = report.latest_crash
            c_meta = QVBoxLayout()
            c_meta.setSpacing(tokens.SPACE_XS)
            c_meta.setAlignment(Qt.AlignmentFlag.AlignTop)
            src_lbl = QLabel(f"Source: {c.source_name}", crash_card)
            src_lbl.setWordWrap(True)
            c_meta.addWidget(src_lbl)
            imp_lbl = QLabel(f"Imported: {c.imported_at}  |  Kind: {c.kind}", crash_card)
            imp_lbl.setWordWrap(True)
            c_meta.addWidget(imp_lbl)
            if c.top_suspect:
                susp_lbl = QLabel(f"Top Suspect: {c.top_suspect}", crash_card)
                susp_lbl.setWordWrap(True)
                susp_lbl.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-weight: {tokens.FONT_WEIGHT_SEMIBOLD};")
                c_meta.addWidget(susp_lbl)
            if c.root_cause:
                rc_lbl = QLabel(f"Root Cause: {c.root_cause}", crash_card)
                rc_lbl.setWordWrap(True)
                c_meta.addWidget(rc_lbl)
            cr_layout.addLayout(c_meta)

        self._content_layout.addWidget(crash_card)

        # 6. Mod Inventory Summary Card
        mod_card = QFrame(self._content_widget)
        mod_card.setObjectName("reportModInventoryCard")
        mod_card.setProperty("card", "true")
        mod_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        m_layout = QVBoxLayout(mod_card)
        m_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        m_layout.setSpacing(tokens.SPACE_SM)
        m_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        m_title = QLabel("Modpack Inventory Summary", mod_card)
        mt_font = m_title.font()
        mt_font.setPointSize(tokens.FONT_SIZE_BASE)
        mt_font.setWeight(QFont.Weight.Bold)
        m_title.setFont(mt_font)
        m_layout.addWidget(m_title)

        inv = report.mod_inventory
        inv_grid = QHBoxLayout()
        inv_grid.setSpacing(tokens.SPACE_MD)
        inv_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        inv_grid.addWidget(QLabel(f"Total Mods: {inv.total_mods}", mod_card))
        inv_grid.addWidget(QLabel(f"JAR Files: {inv.total_jars}", mod_card))
        inv_grid.addWidget(QLabel(f"Enabled: {inv.enabled_mods}", mod_card))
        inv_grid.addWidget(QLabel(f"Disabled: {inv.disabled_mods}", mod_card))
        inv_grid.addStretch()
        m_layout.addLayout(inv_grid)
        self._content_layout.addWidget(mod_card)

        self._content_layout.addStretch(1)
        self._content_layout.activate()

    # ------------------------------------------------------------------ Export Actions
    def _on_export_button_clicked(self, fmt: str) -> None:
        if self._current_report is None:
            return
        out_path = self.pick_save_path(fmt)
        if out_path:
            self.export_report(fmt, out_path)

    def export_report(self, fmt: str, out_path: str) -> None:
        """Asynchronously export diagnostic report to HTML or JSON."""
        target_path = Path(out_path).resolve()
        if target_path.exists():
            if not self.confirm_overwrite(str(target_path)):
                self.export_failed.emit("CANCELLED", "Export cancelled by user")
                return

        if self._current_report is None:
            self.export_failed.emit("NO_REPORT", "No report available to export")
            return

        self._task_manager.start_export_report(
            report=self._current_report,
            fmt=fmt,
            out_path=str(target_path),
            on_finished=self.export_finished.emit,
            on_failed=self.export_failed.emit,
        )
