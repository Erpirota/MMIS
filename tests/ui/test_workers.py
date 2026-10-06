"""Asynchronous worker, background scanning, and concurrency tests (A5)."""

from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import MagicMock

from mmis.domain.models import ScanResult
from mmis.domain.profiles import Profile, ProfileStatus, ProfileSummary, ScanRecord, ScanSummary
from mmis.services.profiles import ProfileService


def test_scan_worker_threadpool_and_signals(qapp):
    from PySide6.QtCore import QThreadPool
    from mmis.ui.workers import ScanWorker

    mock_service = MagicMock(spec=ProfileService)
    dummy_summary = ScanSummary(
        id=1, profile_id=1, scanned_at="2026-10-05T12:00:00Z", loader="fabric", mc_version="1.20.1",
        jar_count=1, mod_count=1, error_count=0, warning_count=0, info_count=0, label=None, pinned=False,
    )
    dummy_record = ScanRecord(
        summary=dummy_summary,
        result=ScanResult(scanned_path="/p", mods_dir="/p/mods"),
        analysis=None,
    )
    mock_service.scan_profile.return_value = dummy_record

    results = []
    worker = ScanWorker(mock_service, "TestPack")
    worker.signals.finished.connect(lambda name, rec: results.append((name, rec)))

    pool = QThreadPool()
    pool.start(worker)
    pool.waitForDone(2000)

    # Allow queued cross-thread signal to dispatch on main thread event loop
    qapp.processEvents()

    assert len(results) == 1
    assert results[0][0] == "TestPack"
    assert results[0][1] == dummy_record


def test_scan_does_not_block_gui_event_loop(qapp, tmp_path: Path):
    from PySide6.QtCore import QCoreApplication, QTimer
    from mmis.ui.shell import MainWindow

    mock_service = MagicMock(spec=ProfileService)
    mock_service.list_profiles.return_value = [
        ProfileSummary(
            id=1, name="AsyncPack", path=str(tmp_path), status=ProfileStatus.OK,
            last_scanned_at="2026-10-05T12:00:00Z", last_loader="fabric", last_mc_version="1.20.1",
            mod_count=5, error_count=0, warning_count=0, scan_count=1,
        )
    ]
    mock_service.get_profile.return_value = Profile(
        id=1, name="AsyncPack", path=str(tmp_path), created_at="", updated_at="",
    )
    mock_service.get_history.return_value = []

    def slow_scan(name, **kwargs):
        time.sleep(0.3)
        dummy_summary = ScanSummary(
            id=2, profile_id=1, scanned_at="2026-10-05T12:05:00Z", loader="fabric", mc_version="1.20.1",
            jar_count=5, mod_count=5, error_count=0, warning_count=0, info_count=0, label=None, pinned=False,
        )
        return ScanRecord(
            summary=dummy_summary,
            result=ScanResult(scanned_path=str(tmp_path), mods_dir=str(tmp_path / "mods")),
            analysis=None,
        )

    mock_service.scan_profile.side_effect = slow_scan

    window = MainWindow(service=mock_service, data_dir=tmp_path)
    window.show()

    # Trigger scan
    timer_fired = []
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: timer_fired.append(True))
    timer.start()

    window.scan_profile("AsyncPack")
    assert window.task_manager.is_scanning("AsyncPack") is True
    assert window.dashboard_page._state_card.isVisible()  # Loading state shown

    # Process events until finished
    start_t = time.time()
    while window.task_manager.is_scanning("AsyncPack") and time.time() - start_t < 3.0:
        QCoreApplication.processEvents()
        time.sleep(0.02)

    timer.stop()
    assert len(timer_fired) >= 2, "Event loop did not fire timers during background scan!"
    assert window.task_manager.is_scanning("AsyncPack") is False
    assert window.dashboard_page._scroll_area.isVisible()  # Ready state shown

    window.close()


def test_scan_exception_shows_error_toast_not_crash(qapp, tmp_path: Path):
    from PySide6.QtCore import QCoreApplication
    from mmis.ui.shell import MainWindow

    mock_service = MagicMock(spec=ProfileService)
    mock_service.list_profiles.return_value = [
        ProfileSummary(
            id=1, name="FailPack", path=str(tmp_path), status=ProfileStatus.OK,
            last_scanned_at=None, last_loader=None, last_mc_version=None,
            mod_count=0, error_count=0, warning_count=0, scan_count=0,
        )
    ]
    mock_service.get_profile.return_value = Profile(
        id=1, name="FailPack", path=str(tmp_path), created_at="", updated_at="",
    )
    mock_service.scan_profile.side_effect = RuntimeError("Simulated internal scan failure")

    window = MainWindow(service=mock_service, data_dir=tmp_path)
    window.show()

    window.scan_profile("FailPack")

    start_t = time.time()
    while window.task_manager.is_scanning("FailPack") and time.time() - start_t < 3.0:
        QCoreApplication.processEvents()
        time.sleep(0.02)

    assert window.dashboard_page._state_card.isVisible()
    assert "RUNTIMEERROR" in window.dashboard_page._state_card._code_label.text()
    window.close()
