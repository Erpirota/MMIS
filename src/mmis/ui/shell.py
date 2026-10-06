"""Application shell main window, navigation orchestrator, and transition coordinator."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mmis.domain.profiles import ProfileSummary, ScanRecord, ScanSummary
from mmis.services.paths import resolve_data_dir
from mmis.services.profiles import ProfileService
from mmis.services.settings import load_settings, save_settings
from mmis.ui import strings
from mmis.ui.components.sidebar import Sidebar
from mmis.ui.components.topbar import TopBar
from mmis.ui.pages.backup import BackupPage
from mmis.ui.pages.compare import ComparePage
from mmis.ui.pages.crashes import CrashesPage
from mmis.ui.pages.dashboard import DashboardPage
from mmis.ui.pages.issues import IssuesPage
from mmis.ui.pages.mods import ModsPage
from mmis.ui.pages.profiles import ProfilesPage
from mmis.ui.pages.reports import ReportsPage
from mmis.ui.theme import icons, qss, tokens
from mmis.ui.widgets.toast import ToastOverlay
from mmis.ui.workers import TaskManager


class MainWindow(QMainWindow):
    """Main application shell window managing pages, navigation, and background scans."""

    def __init__(
        self,
        service: ProfileService,
        data_dir: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.data_dir = resolve_data_dir(data_dir)
        self.task_manager = TaskManager(self.service)

        self._active_profile_name: str | None = None
        self._cached_records: dict[str, ScanRecord] = {}

        # Load persisted settings
        self._settings = load_settings(self.data_dir)

        self._setup_window_properties()
        self._build_ui()
        self._setup_shortcuts()
        self._load_profiles()

    def _setup_window_properties(self) -> None:
        self.setWindowTitle(strings.APP_TITLE)
        self.setMinimumSize(QSize(960, 600))

        # Restore window geometry from settings
        width = int(self._settings.get("window_width") or 1440)
        height = int(self._settings.get("window_height") or 900)
        self.resize(width, height)

        pos_x = self._settings.get("window_x")
        pos_y = self._settings.get("window_y")
        if pos_x is not None and pos_y is not None:
            self.move(int(pos_x), int(pos_y))

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Left Sidebar
        self.sidebar = Sidebar(self)
        self.sidebar.page_selected.connect(self._on_sidebar_nav)
        root_layout.addWidget(self.sidebar)

        # Main Content Layout (TopBar + Stacked Pages)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        main_layout.setSpacing(tokens.SPACE_MD)

        # TopBar
        self.topbar = TopBar(self)
        self.topbar.profile_changed.connect(self.set_active_profile)
        self.topbar.scan_requested.connect(self.scan_profile)
        main_layout.addWidget(self.topbar)

        # Stacked Pages
        self.stack = QStackedWidget(self)

        self.dashboard_page = DashboardPage(self)
        self.dashboard_page.scan_requested.connect(self._scan_active_profile)
        self.dashboard_page.add_profile_requested.connect(lambda: self.switch_page("profiles"))
        self.dashboard_page.change_path_requested.connect(lambda: self.switch_page("profiles"))

        self.profiles_page = ProfilesPage(self.service, self)
        self.profiles_page.scan_requested.connect(self.scan_profile)
        self.profiles_page.profiles_changed.connect(self._load_profiles)

        self.mods_page = ModsPage(self)
        self.mods_page.scan_requested.connect(self._scan_active_profile)

        self.issues_page = IssuesPage(self)
        self.issues_page.scan_requested.connect(self._scan_active_profile)
        self.issues_page.navigate_to_mod.connect(self._on_navigate_to_mod)

        self.compare_page = ComparePage(self.service, self)
        self.compare_page.scan_requested.connect(self._scan_active_profile)
        self.compare_page.scans_updated.connect(self._load_profiles)

        self.crashes_page = CrashesPage(self.service, self.task_manager, self)
        self.reports_page = ReportsPage(self.service, self.task_manager, self)
        self.backup_page = BackupPage(self.service, self.data_dir, self.task_manager, self)

        self.stack.addWidget(self.dashboard_page)  # index 0
        self.stack.addWidget(self.profiles_page)   # index 1
        self.stack.addWidget(self.mods_page)       # index 2
        self.stack.addWidget(self.issues_page)     # index 3
        self.stack.addWidget(self.compare_page)    # index 4
        self.stack.addWidget(self.crashes_page)    # index 5
        self.stack.addWidget(self.reports_page)    # index 6
        self.stack.addWidget(self.backup_page)     # index 7

        main_layout.addWidget(self.stack, 1)
        root_layout.addLayout(main_layout, 1)

        # Toast notification overlay
        self.toast_overlay = ToastOverlay(self)

        # Restore active page
        saved_page = self._settings.get("active_page") or "dashboard"
        self.switch_page(saved_page)

    def _setup_shortcuts(self) -> None:
        # Ctrl+1: Dashboard
        act_dash = QAction(self)
        act_dash.setShortcut(QKeySequence("Ctrl+1"))
        act_dash.triggered.connect(lambda: self.switch_page("dashboard"))
        self.addAction(act_dash)

        # Ctrl+2: Profiles
        act_prof = QAction(self)
        act_prof.setShortcut(QKeySequence("Ctrl+2"))
        act_prof.triggered.connect(lambda: self.switch_page("profiles"))
        self.addAction(act_prof)

        # Ctrl+3: Mods
        act_mods = QAction(self)
        act_mods.setShortcut(QKeySequence("Ctrl+3"))
        act_mods.triggered.connect(lambda: self.switch_page("mods"))
        self.addAction(act_mods)

        # Ctrl+4: Issues
        act_issues = QAction(self)
        act_issues.setShortcut(QKeySequence("Ctrl+4"))
        act_issues.triggered.connect(lambda: self.switch_page("issues"))
        self.addAction(act_issues)

        # Ctrl+5: Compare
        act_compare = QAction(self)
        act_compare.setShortcut(QKeySequence("Ctrl+5"))
        act_compare.triggered.connect(lambda: self.switch_page("compare"))
        self.addAction(act_compare)

        # Ctrl+6: Crash Logs
        act_crashes = QAction(self)
        act_crashes.setShortcut(QKeySequence("Ctrl+6"))
        act_crashes.triggered.connect(lambda: self.switch_page("crashes"))
        self.addAction(act_crashes)

        # Ctrl+7: Reports
        act_reports = QAction(self)
        act_reports.setShortcut(QKeySequence("Ctrl+7"))
        act_reports.triggered.connect(lambda: self.switch_page("reports"))
        self.addAction(act_reports)

        # Ctrl+8: Backup
        act_backup = QAction(self)
        act_backup.setShortcut(QKeySequence("Ctrl+8"))
        act_backup.triggered.connect(lambda: self.switch_page("backup"))
        self.addAction(act_backup)

        # Ctrl+F: Focus Search on Mods
        act_search = QAction(self)
        act_search.setShortcut(QKeySequence("Ctrl+F"))
        act_search.triggered.connect(self._on_search_shortcut)
        self.addAction(act_search)

        # Ctrl+R: Scan Active Profile
        act_scan = QAction(self)
        act_scan.setShortcut(QKeySequence("Ctrl+R"))
        act_scan.triggered.connect(self._scan_active_profile)
        self.addAction(act_scan)

    def _on_search_shortcut(self) -> None:
        self.switch_page("mods")
        self.mods_page.focus_search()

    def _on_navigate_to_mod(self, mod_id: str) -> None:
        self.switch_page("mods")
        self.mods_page.select_mod(mod_id)

    def _scan_active_profile(self) -> None:
        if self._active_profile_name:
            self.scan_profile(self._active_profile_name)

    def _load_profiles(self) -> None:
        summaries = self.service.list_profiles()
        profile_names = [p.name for p in summaries]

        target_profile = self._active_profile_name
        if not target_profile:
            target_profile = self._settings.get("last_profile")

        if target_profile not in profile_names:
            target_profile = profile_names[0] if profile_names else None

        self.topbar.set_profiles(profile_names, target_profile)

        if target_profile:
            self.set_active_profile(target_profile)
        else:
            self._active_profile_name = None
            self.dashboard_page.show_empty_no_profiles()
            self.mods_page.show_empty_no_profiles()
            self.issues_page.show_empty_no_profiles()
            self.compare_page.set_profile(None)
            self.crashes_page.set_active_profile(None)
            self.reports_page.set_active_profile(None)

    def set_active_profile(self, name: str) -> None:
        if not name:
            return

        self._active_profile_name = name
        self._settings["last_profile"] = name
        self.topbar.set_current_profile(name)

        # Reset Mods filters when switching active profile
        self.mods_page.reset_filters()
        self.crashes_page.set_active_profile(name)
        self.reports_page.set_active_profile(name)

        # Check latest scan snapshot for profile
        try:
            profile = self.service.get_profile(name)
            if not os.path.exists(profile.path) or not os.path.isdir(profile.path):
                self.dashboard_page.show_error("PROFILE_PATH_MISSING", strings.ERR_PROFILE_PATH_MISSING)
                self.mods_page.show_error("PROFILE_PATH_MISSING", strings.ERR_PROFILE_PATH_MISSING)
                self.issues_page.show_error("PROFILE_PATH_MISSING", strings.ERR_PROFILE_PATH_MISSING)
                self.compare_page.set_profile(name)
                return

            scans = self.service.get_history(name, limit=1)
            if not scans:
                self.dashboard_page.show_never_scanned(name)
                self.mods_page.show_never_scanned(name)
                self.issues_page.show_never_scanned(name)
                self.compare_page.set_profile(name)
                return

            latest_summary = scans[0]
            if name in self._cached_records:
                cached = self._cached_records[name]
                if cached.summary.id == latest_summary.id:
                    self._display_record(name, cached)
                    return

            # Load snapshot record in worker to avoid GUI lag
            self.dashboard_page.show_loading()
            self.mods_page.show_loading()
            self.issues_page.show_loading()

            def _on_record_loaded(p_name: str, rec: ScanRecord) -> None:
                if p_name.casefold() == (self._active_profile_name or "").casefold():
                    self._cached_records[p_name] = rec
                    self._display_record(p_name, rec)

            def _on_record_failed(p_name: str, code: str, msg: str) -> None:
                if p_name.casefold() == (self._active_profile_name or "").casefold():
                    self.dashboard_page.show_error(code, msg)
                    self.mods_page.show_error(code, msg)
                    self.issues_page.show_error(code, msg)

            self.task_manager.start_load_record(
                name,
                latest_summary.id,
                on_finished=_on_record_loaded,
                on_failed=_on_record_failed,
            )

        except Exception as exc:
            self.dashboard_page.show_error(type(exc).__name__.upper(), str(exc))
            self.mods_page.show_error(type(exc).__name__.upper(), str(exc))
            self.issues_page.show_error(type(exc).__name__.upper(), str(exc))

    def _display_record(self, profile_name: str, record: ScanRecord) -> None:
        history = self.service.get_history(profile_name, limit=5)
        self.dashboard_page.show_data(record, history)
        self.mods_page.set_data(record)
        self.issues_page.set_data(record)
        self.compare_page.set_profile(profile_name)

    def scan_profile(self, name: str) -> None:
        """Trigger non-blocking background scan for profile."""
        if self.task_manager.is_scanning(name):
            return

        def _on_started(p_name: str) -> None:
            self.topbar.set_scanning(True)
            self.profiles_page.set_scanning_profile(p_name, True)
            if p_name.casefold() == (self._active_profile_name or "").casefold():
                self.dashboard_page.show_loading()
                self.mods_page.show_loading()
                self.issues_page.show_loading()
            self.toast_overlay.show_toast(
                title=strings.APP_SHORT_TITLE,
                message=strings.TOAST_SCAN_STARTED.format(name=p_name),
                kind="info",
            )

        def _on_finished(p_name: str, record: ScanRecord) -> None:
            self.topbar.set_scanning(False)
            self.profiles_page.set_scanning_profile(p_name, False)
            self.profiles_page.refresh()

            self._cached_records[p_name] = record

            # Only update current view if profile is still active
            if p_name.casefold() == (self._active_profile_name or "").casefold():
                self._display_record(p_name, record)

            self.toast_overlay.show_toast(
                title=strings.APP_SHORT_TITLE,
                message=strings.TOAST_SCAN_SUCCESS.format(name=p_name),
                kind="success",
            )

        def _on_failed(p_name: str, code: str, msg: str) -> None:
            self.topbar.set_scanning(False)
            self.profiles_page.set_scanning_profile(p_name, False)
            self.profiles_page.refresh()

            if p_name.casefold() == (self._active_profile_name or "").casefold():
                self.dashboard_page.show_error(code, msg)
                self.mods_page.show_error(code, msg)
                self.issues_page.show_error(code, msg)

            self.toast_overlay.show_toast(
                title=strings.TOAST_ERROR_TITLE,
                message=msg,
                kind="error",
                code=code,
            )

        self.task_manager.start_scan(
            name,
            on_started=_on_started,
            on_finished=_on_finished,
            on_failed=_on_failed,
        )

    def _on_sidebar_nav(self, page_id: str) -> None:
        self.switch_page(page_id)

    def switch_page(self, page_id: str) -> None:
        page_map = {
            "dashboard": (0, self.dashboard_page),
            "profiles": (1, self.profiles_page),
            "mods": (2, self.mods_page),
            "issues": (3, self.issues_page),
            "compare": (4, self.compare_page),
            "crashes": (5, self.crashes_page),
            "reports": (6, self.reports_page),
            "backup": (7, self.backup_page),
        }
        if page_id not in page_map:
            return

        target_idx, target_widget = page_map[page_id]
        current_idx = self.stack.currentIndex()
        if current_idx == target_idx:
            return

        self._settings["active_page"] = page_id
        self.sidebar.select_page(page_id)

        duration = tokens.get_animation_duration(tokens.DURATION_NORMAL_MS)
        if duration == 0:
            self.stack.setCurrentIndex(target_idx)
            return

        # Smooth cross-fade transition
        effect = QGraphicsOpacityEffect(target_widget)
        target_widget.setGraphicsEffect(effect)
        self.stack.setCurrentIndex(target_idx)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.finished.connect(lambda: target_widget.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def reload_from_restored_data(self) -> None:
        """Called after a database restore to refresh all cached state and UI pages."""
        self._settings = load_settings(self.data_dir)
        self._cached_records.clear()
        self._load_profiles()
        self.profiles_page.refresh()
        self.crashes_page.refresh()
        self.reports_page.refresh()
        self.backup_page.refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # Responsive sidebar collapse below 1100 px width
        if self.width() < 1100:
            self.sidebar.set_collapsed(True)
        else:
            self.sidebar.set_collapsed(False)

        # Reposition floating toast overlay
        self.toast_overlay.reposition()

        # Update mods drawer layout mode (split vs overlay)
        self.mods_page.update_layout_mode()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Save geometry and current state
        self._settings["window_width"] = self.width()
        self._settings["window_height"] = self.height()
        self._settings["window_x"] = self.x()
        self._settings["window_y"] = self.y()
        self._settings["sidebar_collapsed"] = self.width() < 1100
        save_settings(self._settings, self.data_dir)

        # Bounded wait for active workers to avoid partial writes
        if self.task_manager.is_scanning():
            self.task_manager.wait_for_all(timeout_ms=2500)

        super().closeEvent(event)
