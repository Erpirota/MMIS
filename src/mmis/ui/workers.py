"""Asynchronous workers and threadpool orchestration for disk and scan operations."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from mmis.domain.errors import (
    DuplicateProfileError,
    ProfileNotFoundError,
    ProfilePathMissingError,
    ScanNotFoundError,
    StorageError,
)
from mmis.domain.profiles import ScanRecord
from mmis.services.paths import resolve_data_dir
from mmis.services.profiles import ProfileService

logger = logging.getLogger("mmis.ui")


def setup_ui_logging(data_dir: str | Path | None = None) -> None:
    """Configure rotating log file in the MMIS data directory."""
    base_dir = resolve_data_dir(data_dir)
    log_file = base_dir / "mmis_app.log"

    handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        logger.addHandler(handler)


class WorkerSignals(QObject):
    """Signals dispatched by asynchronous background tasks."""

    started = Signal(str)
    finished = Signal(str, object)
    failed = Signal(str, str, str)  # profile_name, error_code, user_message


class ScanWorker(QRunnable):
    """Background task executing modpack scans via ProfileService."""

    def __init__(self, service: ProfileService, profile_name: str) -> None:
        super().__init__()
        self.service = service
        self.profile_name = profile_name
        self.signals = WorkerSignals()

    def run(self) -> None:
        self.signals.started.emit(self.profile_name)
        try:
            record: ScanRecord = self.service.scan_profile(self.profile_name)
            self.signals.finished.emit(self.profile_name, record)
        except ProfilePathMissingError as exc:
            logger.warning("Profile directory missing for '%s': %s", self.profile_name, exc)
            self.signals.failed.emit(
                self.profile_name,
                "PROFILE_PATH_MISSING",
                str(exc),
            )
        except StorageError as exc:
            logger.error("Storage error scanning profile '%s': %s", self.profile_name, exc)
            self.signals.failed.emit(
                self.profile_name,
                "STORAGE_ERROR",
                str(exc),
            )
        except Exception as exc:
            logger.exception("Unexpected error scanning profile '%s'", self.profile_name)
            self.signals.failed.emit(
                self.profile_name,
                type(exc).__name__.upper(),
                str(exc),
            )


class LoadRecordWorker(QRunnable):
    """Background task loading persisted scan records from SQLite."""

    def __init__(self, service: ProfileService, profile_name: str, scan_id: int) -> None:
        super().__init__()
        self.service = service
        self.profile_name = profile_name
        self.scan_id = scan_id
        self.signals = WorkerSignals()

    def run(self) -> None:
        self.signals.started.emit(self.profile_name)
        try:
            record = self.service.get_scan_record(self.profile_name, self.scan_id)
            self.signals.finished.emit(self.profile_name, record)
        except Exception as exc:
            logger.exception("Failed to load scan record %d for '%s'", self.scan_id, self.profile_name)
            self.signals.failed.emit(
                self.profile_name,
                type(exc).__name__.upper(),
                str(exc),
            )


class TaskManager:
    """Coordinates threadpool tasks, enforcing single-scan concurrency rules."""

    def __init__(self, service: ProfileService, thread_pool: QThreadPool | None = None) -> None:
        self.service = service
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._active_scans: set[str] = set()
        self._workers: set[Any] = set()

    def is_scanning(self, profile_name: str | None = None) -> bool:
        if profile_name is None:
            return len(self._active_scans) > 0
        return profile_name.casefold() in (p.casefold() for p in self._active_scans)

    def active_scans(self) -> set[str]:
        return set(self._active_scans)

    def start_scan(
        self,
        profile_name: str,
        on_started: Callable[[str], None] | None = None,
        on_finished: Callable[[str, ScanRecord], None] | None = None,
        on_failed: Callable[[str, str, str], None] | None = None,
    ) -> bool:
        """Launch background scan for profile. Returns False if already scanning."""
        if self.is_scanning(profile_name):
            return False

        self._active_scans.add(profile_name)
        worker = ScanWorker(self.service, profile_name)
        self._workers.add(worker)

        def _handle_finished(name: str, record: ScanRecord) -> None:
            self._active_scans.discard(name)
            self._workers.discard(worker)
            if on_finished:
                on_finished(name, record)

        def _handle_failed(name: str, code: str, msg: str) -> None:
            self._active_scans.discard(name)
            self._workers.discard(worker)
            if on_failed:
                on_failed(name, code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)

        self.thread_pool.start(worker)
        return True

    def start_load_record(
        self,
        profile_name: str,
        scan_id: int,
        on_finished: Callable[[str, ScanRecord], None] | None = None,
        on_failed: Callable[[str, str, str], None] | None = None,
    ) -> None:
        worker = LoadRecordWorker(self.service, profile_name, scan_id)
        self._workers.add(worker)

        def _handle_load_finished(p_name: str, rec: ScanRecord) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(p_name, rec)

        def _handle_load_failed(p_name: str, code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(p_name, code, msg)

        worker.signals.finished.connect(_handle_load_finished)
        worker.signals.failed.connect(_handle_load_failed)
        self.thread_pool.start(worker)

    def start_import_crash(
        self,
        path: str,
        profile_name: str | None,
        on_started: Callable[[], None] | None = None,
        on_finished: Callable[[int, bool], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        worker = ImportCrashWorker(self.service, path, profile_name)
        self._workers.add(worker)

        def _handle_finished(cid: int, was_dup: bool) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(cid, was_dup)

        def _handle_failed(code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def is_busy(self) -> bool:
        """Return True if any background scan, import, report, or backup task is running."""
        return self.is_scanning() or bool(self._workers)

    def start_build_report(
        self,
        profile_name: str,
        scan_record: ScanRecord | None = None,
        on_started: Callable[[], None] | None = None,
        on_finished: Callable[[Any], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        worker = BuildReportWorker(self.service, profile_name, scan_record)
        self._workers.add(worker)

        def _handle_finished(report: Any) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(report)

        def _handle_failed(code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def start_export_report(
        self,
        report: Any,
        fmt: str,
        out_path: str,
        on_started: Callable[[], None] | None = None,
        on_finished: Callable[[str], None] | None = None,
        on_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        worker = ExportReportWorker(report, fmt, out_path)
        self._workers.add(worker)

        def _handle_finished(p: str) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(p)

        def _handle_failed(code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def start_create_backup(
        self,
        data_dir: Path | str,
        out_path: Path | str = "",
        on_started: Callable[[str], None] | None = None,
        on_finished: Callable[[str, str], None] | None = None,
        on_failed: Callable[[str, str, str], None] | None = None,
    ) -> None:
        worker = CreateBackupWorker(data_dir, out_path)
        self._workers.add(worker)

        def _handle_finished(op: str, p: str) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(op, p)

        def _handle_failed(op: str, code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(op, code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def start_verify_backup(
        self,
        path: Path | str,
        on_started: Callable[[str], None] | None = None,
        on_finished: Callable[[str, str], None] | None = None,
        on_failed: Callable[[str, str, str], None] | None = None,
    ) -> None:
        worker = VerifyBackupWorker(path)
        self._workers.add(worker)

        def _handle_finished(op: str, p: str) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(op, p)

        def _handle_failed(op: str, code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(op, code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def start_restore_backup(
        self,
        path: Path | str,
        data_dir: Path | str,
        on_started: Callable[[str], None] | None = None,
        on_finished: Callable[[str, str], None] | None = None,
        on_failed: Callable[[str, str, str], None] | None = None,
    ) -> None:
        worker = RestoreBackupWorker(path, data_dir)
        self._workers.add(worker)

        def _handle_finished(op: str, p: str) -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished(op, p)

        def _handle_failed(op: str, code: str, msg: str) -> None:
            self._workers.discard(worker)
            if on_failed:
                on_failed(op, code, msg)

        if on_started:
            worker.signals.started.connect(on_started)
        worker.signals.finished.connect(_handle_finished)
        worker.signals.failed.connect(_handle_failed)
        self.thread_pool.start(worker)

    def wait_for_all(self, timeout_ms: int = 2500) -> bool:
        """Wait for active workers to finish cleanly before window closure."""
        return self.thread_pool.waitForDone(timeout_ms)


class ImportCrashSignals(QObject):
    """Signals dispatched during asynchronous crash log import."""

    started = Signal()
    finished = Signal(int, bool)  # crash_id, was_duplicate
    failed = Signal(str, str)  # error_code, message


class ImportCrashWorker(QRunnable):
    """Background task importing, hashing, and analyzing crash log files."""

    def __init__(self, service: ProfileService, path: str, profile_name: str | None) -> None:
        super().__init__()
        self.service = service
        self.path = path
        self.profile_name = profile_name
        self.signals = ImportCrashSignals()

    def run(self) -> None:
        from mmis.services import crash_view

        self.signals.started.emit()
        p = Path(self.path)
        if not p.exists():
            self.signals.failed.emit("FILE_NOT_FOUND", f"File '{self.path}' not found")
            return
        if p.is_dir():
            self.signals.failed.emit("NOT_A_FILE", f"Path '{self.path}' is a directory")
            return

        try:
            st = p.stat()
            size = st.st_size
        except Exception as exc:
            self.signals.failed.emit("READ_ERROR", str(exc))
            return

        if size == 0:
            self.signals.failed.emit("EMPTY_FILE", f"File '{self.path}' is empty")
            return

        try:
            sha256_val = crash_view.sha256_file(p)
        except Exception as exc:
            self.signals.failed.emit("READ_ERROR", str(exc))
            return

        try:
            dup_id = crash_view.find_duplicate(self.service, sha256_val, self.profile_name)
            if dup_id is not None:
                self.signals.finished.emit(dup_id, True)
                return
        except Exception as exc:
            self.signals.failed.emit("READ_ERROR", str(exc))
            return

        try:
            rec = self.service.import_crash(p, profile_name=self.profile_name)
            self.signals.finished.emit(rec.id, False)
        except Exception as exc:
            self.signals.failed.emit("READ_ERROR", str(exc))


class BuildReportSignals(QObject):
    started = Signal()
    finished = Signal(object)  # DiagnosticReport
    failed = Signal(str, str)  # code, message


class BuildReportWorker(QRunnable):
    def __init__(self, service: ProfileService, profile_name: str, scan_record: ScanRecord | None = None) -> None:
        super().__init__()
        self.service = service
        self.profile_name = profile_name
        self.scan_record = scan_record
        self.signals = BuildReportSignals()

    def run(self) -> None:
        from mmis.services.report import build_diagnostic_report

        self.signals.started.emit()
        try:
            profile = self.service.get_profile(self.profile_name)
            scan_rec = self.scan_record
            if scan_rec is None:
                scan_rec = self.service.get_latest_scan_record(self.profile_name)
            if scan_rec is None:
                self.signals.failed.emit("NO_SCAN", f"Profile '{self.profile_name}' has no scans.")
                return
            latest_crash = self.service.get_latest_crash(self.profile_name)
            report = build_diagnostic_report(
                profile_name=profile.name,
                scan_record=scan_rec,
                profile_path=profile.path,
                latest_crash=latest_crash,
            )
            self.signals.finished.emit(report)
        except Exception as exc:
            self.signals.failed.emit(type(exc).__name__.upper(), str(exc))


class ExportReportSignals(QObject):
    started = Signal()
    finished = Signal(str)  # out_path
    failed = Signal(str, str)  # code, message


class ExportReportWorker(QRunnable):
    def __init__(self, report: Any, fmt: str, out_path: str) -> None:
        super().__init__()
        self.report = report
        self.fmt = fmt.lower()
        self.out_path = out_path
        self.signals = ExportReportSignals()

    def run(self) -> None:
        from mmis.services.export import export_report_html, export_report_json

        self.signals.started.emit()
        try:
            if self.fmt == "json":
                content = export_report_json(self.report)
            else:
                content = export_report_html(self.report)
            target = Path(self.out_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self.signals.finished.emit(str(target))
        except Exception as exc:
            self.signals.failed.emit("WRITE_ERROR", str(exc))


class BackupOpSignals(QObject):
    started = Signal(str)  # op
    finished = Signal(str, str)  # op, path
    failed = Signal(str, str, str)  # op, code, message


class CreateBackupWorker(QRunnable):
    def __init__(self, data_dir: Path | str, out_path: Path | str = "") -> None:
        super().__init__()
        self.data_dir = data_dir
        self.out_path = out_path
        self.signals = BackupOpSignals()

    def run(self) -> None:
        from mmis.services.backup import create_backup
        from mmis.services.backup_catalog import new_backup_path

        self.signals.started.emit("create")
        try:
            target = self.out_path or new_backup_path(self.data_dir)
            created = create_backup(data_dir=self.data_dir, out_path=target, force=True)
            self.signals.finished.emit("create", str(created))
        except Exception as exc:
            self.signals.failed.emit("create", type(exc).__name__.upper(), str(exc))


class VerifyBackupWorker(QRunnable):
    def __init__(self, path: Path | str) -> None:
        super().__init__()
        self.path = Path(path).resolve()
        self.signals = BackupOpSignals()

    def run(self) -> None:
        from mmis.domain.errors import BackupInvalidError
        from mmis.services.backup import verify_backup

        self.signals.started.emit("verify")
        try:
            verify_backup(self.path)
            self.signals.finished.emit("verify", str(self.path))
        except BackupInvalidError as exc:
            self.signals.failed.emit("verify", exc.reason, str(exc))
        except Exception as exc:
            self.signals.failed.emit("verify", type(exc).__name__.upper(), str(exc))


class RestoreBackupWorker(QRunnable):
    def __init__(self, path: Path | str, data_dir: Path | str) -> None:
        super().__init__()
        self.path = Path(path).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.signals = BackupOpSignals()

    def run(self) -> None:
        from mmis.domain.errors import BackupInvalidError
        from mmis.services.backup import restore_backup

        self.signals.started.emit("restore")
        try:
            restore_backup(self.path, data_dir=self.data_dir)
            self.signals.finished.emit("restore", str(self.path))
        except BackupInvalidError as exc:
            self.signals.failed.emit("restore", exc.reason, str(exc))
        except Exception as exc:
            self.signals.failed.emit("restore", type(exc).__name__.upper(), str(exc))

