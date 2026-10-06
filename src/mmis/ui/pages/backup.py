"""Backup page for creating, verifying, and restoring full application archives."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable
import zipfile

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
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

from mmis.domain.errors import BackupInvalidError
from mmis.services import backup_catalog
from mmis.services.backup import verify_backup
from mmis.services.backup_catalog import BackupEntry, backups_dir
from mmis.services.crash_view import format_imported_at
from mmis.services.profiles import ProfileService
from mmis.ui import strings
from mmis.ui.theme import icons, tokens
from mmis.ui.widgets.confirm_dialog import ConfirmDialog
from mmis.ui.widgets.state_card import StateCard
from mmis.ui.widgets.status_pill import StatusPillWidget
from mmis.ui.workers import TaskManager


class BackupCard(QFrame):
    """List item card displaying backup metadata, verification status, and action buttons."""

    def __init__(
        self,
        entry: BackupEntry,
        status: str,
        on_verify: Callable[[str], None],
        on_restore: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self._status = status
        self._on_verify = on_verify
        self._on_restore = on_restore

        self.setProperty("card", "true")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_SM, tokens.SPACE_MD, tokens.SPACE_SM)
        layout.setSpacing(tokens.SPACE_XS)

        # Top row: Filename + Kind Badge + Verification Status + Action Buttons
        top_row = QHBoxLayout()
        top_row.setSpacing(tokens.SPACE_SM)

        name_lbl = QLabel(entry.file_name, self)
        n_font = name_lbl.font()
        n_font.setWeight(QFont.Weight.DemiBold)
        name_lbl.setFont(n_font)
        top_row.addWidget(name_lbl, 1)

        kind_text = strings.BACKUP_KIND_PRE_RESTORE if entry.kind == "pre_restore" else strings.BACKUP_KIND_MANUAL
        kind_lbl = QLabel(f"[{kind_text}]", self)
        kind_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
        top_row.addWidget(kind_lbl)

        # Verification Status Pill
        if not entry.manifest_ok:
            stat_pill = StatusPillWidget(status="error", label="Unreadable", parent=self)
            top_row.addWidget(stat_pill)
        elif status == "valid":
            stat_pill = StatusPillWidget(status="success", label="Valid", parent=self)
            top_row.addWidget(stat_pill)
        elif status.startswith("invalid:"):
            reason = status.split(":", 1)[1]
            stat_pill = StatusPillWidget(status="error", label=f"Invalid: {reason}", parent=self)
            top_row.addWidget(stat_pill)
        else:
            stat_pill = StatusPillWidget(status="neutral", label="Unverified", parent=self)
            top_row.addWidget(stat_pill)

        # Actions: Verify button
        verify_btn = QPushButton("Verify", self)
        verify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        verify_btn.clicked.connect(lambda: self._on_verify(str(self.entry.path)))
        top_row.addWidget(verify_btn)

        # Actions: Restore button (only if manifest is readable)
        if entry.manifest_ok:
            restore_btn = QPushButton("Restore", self)
            restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            restore_btn.clicked.connect(lambda: self._on_restore(str(self.entry.path)))
            top_row.addWidget(restore_btn)

        layout.addLayout(top_row)

        # Bottom row: Created At, Size, App/Schema versions
        bot_row = QHBoxLayout()
        bot_row.setSpacing(tokens.SPACE_MD)

        created_text = format_imported_at(entry.created_at) if entry.created_at else entry.modified_at[:16].replace("T", " ") + " UTC"
        date_lbl = QLabel(f"Created: {created_text}", self)
        date_lbl.setStyleSheet(f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.FONT_SIZE_XS}px;")
        bot_row.addWidget(date_lbl)

        size_kb = entry.size_bytes / 1024
        size_str = f"{size_kb / 1024:.2f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"
        size_lbl = QLabel(f"Size: {size_str}", self)
        size_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
        bot_row.addWidget(size_lbl)

        if entry.manifest_ok:
            ver_str = f"App: {entry.app_version or '-'} | Schema: v{entry.schema_version or '-'}"
            ver_lbl = QLabel(ver_str, self)
            ver_lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.FONT_SIZE_XS}px;")
            bot_row.addWidget(ver_lbl)
        else:
            err_lbl = QLabel(f"Error: {entry.error or 'Corrupt'}", self)
            err_lbl.setStyleSheet(f"color: {tokens.COLOR_ERROR}; font-size: {tokens.FONT_SIZE_XS}px;")
            bot_row.addWidget(err_lbl)

        bot_row.addStretch()
        layout.addLayout(bot_row)

        self.setStyleSheet(f"""
            QFrame[card="true"] {{
                background-color: {tokens.SURFACE_1};
                border: 1px solid {tokens.BORDER_HAIRLINE};
                border-radius: {tokens.RADIUS_MD}px;
            }}
            QFrame[card="true"]:hover {{
                border-color: {tokens.ACCENT};
            }}
        """)


class BackupPage(QWidget):
    """Backup management page for archive creation, validation, and safe restore."""

    operation_finished = Signal(str, str)         # op ("create" | "verify" | "restore"), path
    operation_failed = Signal(str, str, str)     # op, code, message

    def __init__(
        self,
        service: ProfileService,
        data_dir: Path | str,
        task_manager: TaskManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.data_dir = Path(data_dir).resolve()
        self._task_manager = task_manager or TaskManager(service)

        self._entries: list[BackupEntry] = []
        self._verification_statuses: dict[str, str] = {}  # resolved path string -> status
        self._is_busy: bool = False

        # Replaceable callbacks
        self.confirm_restore: Callable[[BackupEntry, dict[str, Any]], bool] = self._default_confirm_restore
        self.pick_file: Callable[[], str | None] = self._default_pick_file
        self.open_folder: Callable[[Path], None] = self._default_open_folder

        self._init_ui()

    def _default_confirm_restore(self, entry: BackupEntry, manifest: dict[str, Any]) -> bool:
        created_str = format_imported_at(str(manifest.get("created_at", ""))) or str(manifest.get("created_at", "Unknown"))
        msg = strings.BACKUP_CONFIRM_RESTORE_MSG.format(
            created_at=created_str,
            app_version=manifest.get("app_version", "Unknown"),
            schema_version=manifest.get("schema_version", "Unknown"),
        )
        dlg = ConfirmDialog(
            title=strings.BACKUP_CONFIRM_RESTORE_TITLE,
            message=msg,
            confirm_label="Restore Database",
            is_destructive=True,
            parent=self,
        )
        return dlg.exec() == ConfirmDialog.DialogCode.Accepted

    def _default_pick_file(self) -> str | None:
        res, _ = QFileDialog.getOpenFileName(
            self,
            "Select Backup Archive",
            "",
            "Backup Archives (*.zip);;All Files (*.*)",
        )
        return res if res else None

    def _default_open_folder(self, folder_path: Path) -> None:
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD, tokens.SPACE_MD)
        root_layout.setSpacing(tokens.SPACE_MD)

        # Header Bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(tokens.SPACE_MD)

        title_col = QVBoxLayout()
        title_col.setSpacing(tokens.SPACE_XS)
        self._title_label = QLabel(strings.BACKUP_TITLE, self)
        self._title_label.setProperty("heading", "true")
        t_font = self._title_label.font()
        t_font.setPointSize(tokens.FONT_SIZE_XL)
        t_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(t_font)
        title_col.addWidget(self._title_label)

        self._subtitle_label = QLabel(strings.BACKUP_SUBTITLE, self)
        self._subtitle_label.setProperty("caption", "true")
        self._subtitle_label.setStyleSheet(f"color: {tokens.TEXT_MUTED};")
        title_col.addWidget(self._subtitle_label)
        header_layout.addLayout(title_col, 1)

        # Controls: Actions buttons
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(tokens.SPACE_SM)

        self._create_btn = QPushButton(strings.BACKUP_BTN_CREATE, self)
        self._create_btn.setProperty("primary", "true")
        self._create_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._create_btn.setIcon(icons.get_tinted_icon("backup", color_hex=tokens.TEXT_ON_ACCENT, size=16))
        self._create_btn.clicked.connect(self.create_backup)
        controls_layout.addWidget(self._create_btn)

        self._verify_file_btn = QPushButton(strings.BACKUP_BTN_VERIFY_FILE, self)
        self._verify_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._verify_file_btn.clicked.connect(self._on_verify_file_clicked)
        controls_layout.addWidget(self._verify_file_btn)

        self._restore_file_btn = QPushButton(strings.BACKUP_BTN_RESTORE_FILE, self)
        self._restore_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_file_btn.clicked.connect(self._on_restore_file_clicked)
        controls_layout.addWidget(self._restore_file_btn)

        self._open_folder_btn = QPushButton(strings.BACKUP_BTN_OPEN_FOLDER, self)
        self._open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_folder_btn.setIcon(icons.get_tinted_icon("folder", color_hex=tokens.TEXT_PRIMARY, size=16))
        self._open_folder_btn.clicked.connect(self.open_backups_folder)
        controls_layout.addWidget(self._open_folder_btn)

        header_layout.addLayout(controls_layout)
        root_layout.addLayout(header_layout)

        # Body Stack: 0 = StateCard, 1 = Scrollable list
        self._body_stack = QStackedWidget(self)

        self._state_card = StateCard(self)
        self._body_stack.addWidget(self._state_card)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet("background: transparent;")

        self._list_content = QWidget()
        self._list_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._list_layout = QVBoxLayout(self._list_content)
        self._list_layout.setContentsMargins(0, 0, tokens.SPACE_MD, 0)
        self._list_layout.setSpacing(tokens.SPACE_SM)
        self._list_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self._scroll_area.setWidget(self._list_content)

        self._body_stack.addWidget(self._scroll_area)
        root_layout.addWidget(self._body_stack, 1)

        self.refresh()

    # ------------------------------------------------------------------ Query & State
    def is_busy(self) -> bool:
        """Return True if an operation is currently being executed."""
        return self._is_busy

    def visible_backup_paths(self) -> list[str]:
        """Return list of discovered backup file paths, newest first."""
        return [str(e.path.resolve()) for e in self._entries]

    def verification_status(self, path: str) -> str:
        """Return cached verification status for a specific archive path."""
        norm_path = str(Path(path).resolve())
        return self._verification_statuses.get(norm_path, "unverified")

    def open_backups_folder(self) -> None:
        """Open managed backups folder in file manager."""
        self.open_folder(backups_dir(self.data_dir))

    def refresh(self) -> None:
        """Enumerate backups in data_dir and update display."""
        try:
            self._entries = backup_catalog.list_backups(self.data_dir)
        except Exception:
            self._entries = []

        if not self._entries:
            self._state_card.show_empty(
                title=strings.BACKUP_EMPTY_TITLE,
                description=strings.BACKUP_EMPTY_DESC,
                action_text=strings.BACKUP_BTN_CREATE,
                on_action=self.create_backup,
                icon_name="backup",
            )
            self._body_stack.setCurrentWidget(self._state_card)
        else:
            self._rebuild_list()
            self._body_stack.setCurrentWidget(self._scroll_area)

    def _rebuild_list(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        for entry in self._entries:
            st = self.verification_status(str(entry.path))
            card = BackupCard(
                entry=entry,
                status=st,
                on_verify=self.verify_backup,
                on_restore=self.restore_backup,
                parent=self._list_content,
            )
            self._list_layout.addWidget(card)

        self._list_layout.activate()
        self._list_content.adjustSize()

    # ------------------------------------------------------------------ Operations
    def create_backup(self) -> None:
        """Create a new validated backup archive in the managed folder."""
        if self._is_busy:
            return

        self._is_busy = True
        self._task_manager.start_create_backup(
            data_dir=self.data_dir,
            on_finished=self._on_create_finished,
            on_failed=self._on_create_failed,
        )

    def _on_create_finished(self, op: str, path: str) -> None:
        self._is_busy = False
        self.refresh()
        self.operation_finished.emit("create", path)

    def _on_create_failed(self, op: str, code: str, msg: str) -> None:
        self._is_busy = False
        self.operation_failed.emit("create", code, msg)

    def _on_verify_file_clicked(self) -> None:
        path = self.pick_file()
        if path:
            self.verify_backup(path)

    def verify_backup(self, path: str) -> None:
        """Verify archive cryptographic hashes and manifest integrity."""
        norm_path = str(Path(path).resolve())
        self._is_busy = True

        def _handle_finished(op: str, p: str) -> None:
            self._is_busy = False
            self._verification_statuses[norm_path] = "valid"
            self._rebuild_list()
            self.operation_finished.emit("verify", norm_path)

        def _handle_failed(op: str, code: str, msg: str) -> None:
            self._is_busy = False
            self._verification_statuses[norm_path] = f"invalid:{code}"
            self._rebuild_list()
            self.operation_failed.emit("verify", code, msg)

        self._task_manager.start_verify_backup(
            path=norm_path,
            on_finished=_handle_finished,
            on_failed=_handle_failed,
        )

    def _on_restore_file_clicked(self) -> None:
        path = self.pick_file()
        if path:
            self.restore_backup(path)

    def restore_backup(self, path: str) -> None:
        """Safely verify, confirm, and restore an archive to data_dir."""
        # 1. Immediate sync check: refuse if any scan, import, or backup op is running
        if self._is_busy or self._task_manager.is_busy():
            self.operation_failed.emit("restore", "BUSY", "Another operation is running.")
            return

        target_path = Path(path).resolve()
        norm_path = str(target_path)

        # 2. Strict pre-validation: verify archive before asking confirmation
        try:
            verify_backup(target_path)
            self._verification_statuses[norm_path] = "valid"
        except BackupInvalidError as exc:
            self._verification_statuses[norm_path] = f"invalid:{exc.reason}"
            self._rebuild_list()
            self.operation_failed.emit("restore", exc.reason, str(exc))
            return
        except Exception as exc:
            self._verification_statuses[norm_path] = f"invalid:{type(exc).__name__.upper()}"
            self._rebuild_list()
            self.operation_failed.emit("restore", type(exc).__name__.upper(), str(exc))
            return

        # 3. Read manifest metadata for confirmation dialog
        try:
            with zipfile.ZipFile(target_path, "r") as zf:
                man_bytes = zf.read("manifest.json")
                manifest = json.loads(man_bytes.decode("utf-8"))
        except Exception as exc:
            self.operation_failed.emit("restore", "INVALID_MANIFEST", str(exc))
            return

        entry = next((e for e in self._entries if e.path.resolve() == target_path), None)
        if entry is None:
            st = target_path.stat()
            entry = BackupEntry(
                path=target_path,
                file_name=target_path.name,
                size_bytes=st.st_size,
                modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                kind="manual",
                manifest_ok=True,
                created_at=manifest.get("created_at"),
                app_version=manifest.get("app_version"),
                schema_version=manifest.get("schema_version"),
                error=None,
            )

        if not self.confirm_restore(entry, manifest):
            self.operation_failed.emit("restore", "CANCELLED", "Restore cancelled by user.")
            return

        # 4. Disable window during restore execution
        win = self.window()
        if win:
            win.setEnabled(False)

        self._is_busy = True
        self._task_manager.start_restore_backup(
            path=target_path,
            data_dir=self.data_dir,
            on_finished=self._on_restore_finished,
            on_failed=self._on_restore_failed,
        )

    def _on_restore_finished(self, op: str, path: str) -> None:
        self._is_busy = False
        win = self.window()
        if win:
            win.setEnabled(True)
            if hasattr(win, "reload_from_restored_data"):
                win.reload_from_restored_data()

        self.refresh()
        self.operation_finished.emit("restore", path)

    def _on_restore_failed(self, op: str, code: str, msg: str) -> None:
        self._is_busy = False
        win = self.window()
        if win:
            win.setEnabled(True)
        self.operation_failed.emit("restore", code, msg)
