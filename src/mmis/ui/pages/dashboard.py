"""Dashboard page rendering modpack health ring, stat cards, and diagnostic highlights."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mmis.domain.models import Severity
from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.pack_view import ModRow, build_pack_view, calculate_pack_health
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.metric_ring import MetricRing
from mmis.ui.widgets.stat_card import StatCard
from mmis.ui.widgets.state_card import PageState, StateCard


def _clear_layout(layout: QLayout) -> None:
    """Recursively detach and delete all widgets from a layout to prevent ghost stacking."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


class DashboardPage(QWidget):
    """Health summary, stat cards, top diagnostic issues, and snapshot history."""

    scan_requested = Signal()
    add_profile_requested = Signal()
    change_path_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._main_layout.setSpacing(tokens.SPACE_LG)

        # Fixed Header title and subtitle (never shifts during loading)
        header_layout = QVBoxLayout()
        header_layout.setSpacing(tokens.SPACE_XXS)

        self._title_label = QLabel(strings.DASHBOARD_TITLE)
        self._title_label.setProperty("heading", "true")
        title_font = self._title_label.font()
        title_font.setPointSize(tokens.FONT_SIZE_XL)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)
        header_layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.DASHBOARD_SUBTITLE)
        self._subtitle_label.setProperty("subheading", "true")
        header_layout.addWidget(self._subtitle_label)

        self._main_layout.addLayout(header_layout)

        # Body Stack: switches cleanly between Scroll Area (READY) and State Card (EMPTY/LOADING/ERROR)
        self._body_stack = QStackedWidget(self)

        # Scrollable content area for populated state
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.viewport().setAutoFillBackground(False)

        self._content_widget = QWidget()
        self._content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._content_layout.setContentsMargins(0, 0, tokens.SPACE_SM, 0)
        self._content_layout.setSpacing(tokens.SPACE_LG)

        # Overview row: Metric Ring on left, 4 StatCards in 2x2 grid on right
        self._overview_frame = QFrame()
        self._overview_frame.setProperty("card", "true")
        overview_layout = QHBoxLayout(self._overview_frame)
        overview_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        overview_layout.setSpacing(tokens.SPACE_LG)

        # Health ring with status word and clean mods counter
        ring_container = QVBoxLayout()
        ring_container.setSpacing(tokens.SPACE_XS)
        ring_container.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._ring = MetricRing(100, label=strings.DASHBOARD_HEALTH_LABEL, status="Healthy", parent=self)
        ring_container.addWidget(self._ring, 0, Qt.AlignmentFlag.AlignCenter)

        self._ring_detail_label = QLabel()
        self._ring_detail_label.setProperty("caption", "true")
        self._ring_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ring_detail_label.setWordWrap(True)
        self._ring_detail_label.setMinimumSize(130, 32)
        ring_container.addWidget(self._ring_detail_label)

        overview_layout.addLayout(ring_container, 0)

        # 4 Stat cards
        stats_grid = QGridLayout()
        stats_grid.setSpacing(tokens.SPACE_MD)

        self._stat_mods = StatCard(strings.DASHBOARD_STAT_MODS, "0", icon_name="package")
        self._stat_jars = StatCard(strings.DASHBOARD_STAT_JARS, "0", icon_name="mods")
        self._stat_issues = StatCard(strings.DASHBOARD_STAT_ISSUES, "0", icon_name="alert_triangle")
        self._stat_loader = StatCard(strings.DASHBOARD_STAT_LOADER, "Unknown", icon_name="folder")

        stats_grid.addWidget(self._stat_mods, 0, 0)
        stats_grid.addWidget(self._stat_jars, 0, 1)
        stats_grid.addWidget(self._stat_issues, 1, 0)
        stats_grid.addWidget(self._stat_loader, 1, 1)

        overview_layout.addLayout(stats_grid, 1)
        self._content_layout.addWidget(self._overview_frame)

        # Lower row: Top Issues card + Recent Snapshots card
        self._bottom_layout = QHBoxLayout()
        self._bottom_layout.setSpacing(tokens.SPACE_LG)

        # Top issues card
        self._issues_frame = QFrame()
        self._issues_frame.setProperty("card", "true")
        self._issues_layout = QVBoxLayout(self._issues_frame)
        self._issues_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._issues_layout.setSpacing(tokens.SPACE_MD)

        issues_title = QLabel(strings.DASHBOARD_TOP_ISSUES_TITLE)
        issues_title.setProperty("section_header", "true")
        self._issues_layout.addWidget(issues_title)

        self._issues_container = QVBoxLayout()
        self._issues_container.setSpacing(tokens.SPACE_SM)
        self._issues_layout.addLayout(self._issues_container)
        self._issues_layout.addStretch()

        self._bottom_layout.addWidget(self._issues_frame, 3)

        # Recent scans card
        self._scans_frame = QFrame()
        self._scans_frame.setProperty("card", "true")
        self._scans_layout = QVBoxLayout(self._scans_frame)
        self._scans_layout.setContentsMargins(tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG, tokens.SPACE_LG)
        self._scans_layout.setSpacing(tokens.SPACE_MD)

        scans_title = QLabel(strings.DASHBOARD_RECENT_SCANS_TITLE)
        scans_title.setProperty("section_header", "true")
        self._scans_layout.addWidget(scans_title)

        self._scans_container = QVBoxLayout()
        self._scans_container.setSpacing(tokens.SPACE_SM)
        self._scans_layout.addLayout(self._scans_container)
        self._scans_layout.addStretch()

        self._bottom_layout.addWidget(self._scans_frame, 2)

        self._content_layout.addLayout(self._bottom_layout)
        self._content_layout.addStretch()

        self._scroll_area.setWidget(self._content_widget)
        self._body_stack.addWidget(self._scroll_area)  # Index 0

        # State card for empty, loading, error
        self._state_card = StateCard(self)
        self._body_stack.addWidget(self._state_card)   # Index 1

        self._main_layout.addWidget(self._body_stack, 1)

        self.set_state(PageState.EMPTY)

    def set_state(self, state: PageState) -> None:
        if state == PageState.READY:
            self._body_stack.setCurrentWidget(self._scroll_area)
        else:
            self._body_stack.setCurrentWidget(self._state_card)

    def show_empty_no_profiles(self) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=strings.STATE_EMPTY_TITLE,
            description=strings.DASHBOARD_EMPTY_DESC,
            action_text=strings.STATE_ACTION_ADD_PROFILE,
            on_action=self.add_profile_requested.emit,
            icon_name="folder",
        )

    def show_never_scanned(self, profile_name: str) -> None:
        self.set_state(PageState.EMPTY)
        self._state_card.show_empty(
            title=f"'{profile_name}' {strings.PROFILES_CARD_NEVER_SCANNED}",
            description=strings.DASHBOARD_NEVER_SCANNED_DESC,
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
        action_text = strings.STATE_ACTION_CHANGE_PATH if code == "PROFILE_PATH_MISSING" else strings.STATE_ACTION_RETRY
        action_cb = self.change_path_requested.emit if code == "PROFILE_PATH_MISSING" else self.scan_requested.emit
        self._state_card.show_error(
            title=strings.STATE_ERROR_TITLE,
            description=message,
            code=code,
            action_text=action_text,
            on_action=action_cb,
        )

    def show_data(
        self,
        record: ScanRecord,
        recent_scans: list[ScanSummary] | None = None,
    ) -> None:
        self.set_state(PageState.READY)

        # Build pack rows
        mod_rows = build_pack_view(record.result, record.analysis)
        total_mods = len(mod_rows)
        enabled_mods = sum(1 for m in mod_rows if not m.is_disabled)
        disabled_mods = sum(1 for m in mod_rows if m.is_disabled)
        nested_mods = sum(1 for m in mod_rows if m.is_nested)

        total_jars = len(record.result.jars)
        disabled_jars = sum(1 for j in record.result.jars if j.is_disabled)
        enabled_jars = total_jars - disabled_jars

        summary = record.summary
        err_count = summary.error_count
        warn_count = summary.warning_count
        info_count = summary.info_count
        # Total diagnostics equals exact stored sum of error + warning + info
        total_findings = err_count + warn_count + info_count

        # Compute defined, explainable pack health metrics via pack_view
        health = calculate_pack_health(mod_rows, summary)
        self._ring.set_score(health.percentage, status=health.pack_status)
        self._ring.set_label(strings.DASHBOARD_HEALTH_LABEL)
        self._ring_detail_label.setText(health.label)

        # Update stat cards
        self._stat_mods.update_metrics(
            str(total_mods),
            f"{enabled_mods} active, {nested_mods} nested, {disabled_mods} disabled",
        )
        self._stat_jars.update_metrics(
            str(total_jars),
            f"{enabled_jars} active, {disabled_jars} disabled",
        )
        err_str = strings.pluralize(err_count, "error")
        warn_str = strings.pluralize(warn_count, "warning")
        self._stat_issues.update_metrics(
            str(total_findings),
            f"{err_str}, {warn_str}, {info_count} info",
        )
        loader_str = (summary.loader or strings.STATE_UNKNOWN).capitalize()
        mc_ver_str = f"Minecraft {summary.mc_version}" if summary.mc_version else "Version Unknown"
        self._stat_loader.update_metrics(loader_str, mc_ver_str)

        # Cleanly rebuild top issues without stacking
        _clear_layout(self._issues_container)

        all_findings = list(record.result.findings)
        if record.analysis is not None:
            all_findings.extend(record.analysis.findings)

        # Filter out informational findings from top issues list
        non_info_findings = [f for f in all_findings if f.severity != Severity.INFO]
        sorted_findings = sorted(
            non_info_findings,
            key=lambda f: (0 if f.severity == Severity.ERROR else 1),
        )

        if not sorted_findings:
            no_issues_lbl = QLabel(strings.DASHBOARD_NO_FINDINGS)
            no_issues_lbl.setProperty("caption", "true")
            self._issues_container.addWidget(no_issues_lbl)
        else:
            for finding in sorted_findings[:3]:
                issue_item = QFrame()
                issue_item.setProperty("card_elevated", "true")
                item_layout = QVBoxLayout(issue_item)
                item_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
                item_layout.setSpacing(tokens.SPACE_XXS)

                # Header: Severity badge + Code
                hdr_layout = QHBoxLayout()
                hdr_layout.setSpacing(tokens.SPACE_SM)

                sev_color = tokens.COLOR_ERROR if finding.severity == Severity.ERROR else tokens.COLOR_WARNING
                sev_label = QLabel(finding.severity.value.upper())
                sev_label.setStyleSheet(f"color: {sev_color}; font-weight: {tokens.FONT_WEIGHT_BOLD}; font-size: {tokens.FONT_SIZE_XS}px;")
                hdr_layout.addWidget(sev_label)

                code_label = QLabel(finding.code)
                code_label.setProperty("code", "true")
                hdr_layout.addWidget(code_label)
                hdr_layout.addStretch()
                item_layout.addLayout(hdr_layout)

                # Message
                msg_lbl = QLabel(finding.message)
                msg_lbl.setProperty("subheading", "true")
                msg_lbl.setWordWrap(True)
                item_layout.addWidget(msg_lbl)

                if finding.suggestion:
                    sug_lbl = QLabel(f"Tip: {finding.suggestion}")
                    sug_lbl.setProperty("caption", "true")
                    sug_lbl.setWordWrap(True)
                    item_layout.addWidget(sug_lbl)

                self._issues_container.addWidget(issue_item)

        # Cleanly rebuild recent scans without stacking
        _clear_layout(self._scans_container)

        if not recent_scans:
            rec_lbl = QLabel(strings.DASHBOARD_NO_SNAPSHOTS)
            rec_lbl.setProperty("caption", "true")
            self._scans_container.addWidget(rec_lbl)
        else:
            for s in recent_scans[:5]:
                scan_row = QFrame()
                scan_row.setProperty("card_elevated", "true")
                s_layout = QHBoxLayout(scan_row)
                s_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)

                date_str = s.scanned_at.split("T")[0] if "T" in s.scanned_at else s.scanned_at[:10]
                time_str = s.scanned_at.split("T")[1][:5] if "T" in s.scanned_at else ""
                date_lbl = QLabel(f"{date_str} {time_str}")
                date_lbl.setProperty("subheading", "true")
                s_layout.addWidget(date_lbl)

                s_layout.addStretch()

                metrics_lbl = QLabel(f"{s.mod_count} mods · {s.error_count} err · {s.warning_count} warn")
                metrics_lbl.setProperty("caption", "true")
                s_layout.addWidget(metrics_lbl)

                self._scans_container.addWidget(scan_row)
