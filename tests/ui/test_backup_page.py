"""UI integration tests for BackupPage."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from mmis.services.profiles import create_profile_service


@pytest.fixture
def seeded_data(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack"
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo_data.py"
    subprocess.run(
        [sys.executable, str(script_path), "--data-dir", str(data_dir), "--pack-dir", str(pack_dir)],
        check=True,
        timeout=30,
    )
    return data_dir


def _pump(app, ms=150):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait_until(app, predicate, timeout_s=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_backup_page_empty_and_create(qapp, seeded_data: Path) -> None:
    from mmis.ui import strings
    from mmis.ui.pages.backup import BackupPage

    service = create_profile_service(seeded_data)
    page = BackupPage(service, seeded_data)
    page.show()
    _pump(qapp, 50)

    # Initially empty
    assert page.visible_backup_paths() == []
    assert strings.BACKUP_EMPTY_TITLE in page._state_card._title_label.text()

    events: list[tuple[str, ...]] = []
    page.operation_finished.connect(lambda op, p: events.append(("ok", op, p)))
    page.operation_failed.connect(lambda op, c, m: events.append(("fail", op, c, m)))

    # Create backup
    page.create_backup()
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=15.0)
    _pump(qapp, 100)

    assert events and events[0][:2] == ("ok", "create")
    created_path = events[0][2]
    assert created_path in page.visible_backup_paths()
    assert page.verification_status(created_path) == "unverified"

    # Verify backup
    events.clear()
    page.verify_backup(created_path)
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=15.0)
    _pump(qapp, 100)

    assert events and events[0][:2] == ("ok", "verify")
    assert page.verification_status(created_path) == "valid"

    page.close()


def test_backup_page_verify_tampered(qapp, seeded_data: Path, tmp_path: Path) -> None:
    from mmis.ui.pages.backup import BackupPage

    service = create_profile_service(seeded_data)
    page = BackupPage(service, seeded_data)
    page.show()
    _pump(qapp, 50)

    events: list[tuple[str, ...]] = []
    page.operation_finished.connect(lambda op, p: events.append(("ok", op, p)))
    page.operation_failed.connect(lambda op, c, m: events.append(("fail", op, c, m)))

    page.create_backup()
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=15.0)
    valid_path = Path(events[0][2])

    # Create tampered copy
    tampered_path = tmp_path / "tampered.zip"
    with zipfile.ZipFile(valid_path, "r") as src_z:
        with zipfile.ZipFile(tampered_path, "w") as dst_z:
            for item in src_z.infolist():
                data = src_z.read(item.filename)
                if item.filename == "mmis.db":
                    data = data[:-1] + bytes([data[-1] ^ 0xFF])
                dst_z.writestr(item, data)

    events.clear()
    page.verify_backup(str(tampered_path))
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=15.0)
    _pump(qapp, 100)

    assert events and events[0][0] == "fail"
    assert events[0][1] == "verify"
    assert "invalid:" in page.verification_status(str(tampered_path))

    page.close()


def test_backup_page_restore_cancelled_and_busy(qapp, seeded_data: Path) -> None:
    from mmis.ui.pages.backup import BackupPage

    service = create_profile_service(seeded_data)
    page = BackupPage(service, seeded_data)
    page.show()
    _pump(qapp, 50)

    events: list[tuple[str, ...]] = []
    page.operation_finished.connect(lambda op, p: events.append(("ok", op, p)))
    page.operation_failed.connect(lambda op, c, m: events.append(("fail", op, c, m)))

    page.create_backup()
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=15.0)
    b_path = events[0][2]

    # 1. Restore cancelled
    events.clear()
    asked = []
    page.confirm_restore = lambda entry, man: (asked.append((entry, man)), False)[1]
    page.restore_backup(b_path)
    _wait_until(qapp, lambda: bool(events) and not page.is_busy(), timeout_s=10.0)

    assert len(asked) == 1
    assert events and events[0][:3] == ("fail", "restore", "CANCELLED")

    # 2. Refusal when busy
    page._is_busy = True
    events.clear()
    page.restore_backup(b_path)
    assert events and events[0][:3] == ("fail", "restore", "BUSY")
    page._is_busy = False

    page.close()


def test_status_pill_width_covers_text_and_padding_v3(qapp) -> None:
    """V3: For each pill label, widget width >= text advance with pill font + horizontal padding."""
    from PySide6.QtGui import QFontMetrics
    from mmis.ui.widgets.status_pill import StatusPillWidget, pill_font

    labels = [
        ("success", "Valid"),
        ("error", "Unreadable"),
        ("neutral", "Unverified"),
        ("error", "Invalid: UNEXPECTED_MEMBER"),
        ("error", "Invalid: HASH_MISMATCH"),
        ("error", "Invalid: SCHEMA_TOO_NEW"),
        ("error", "Invalid: MISSING_MANIFEST"),
        ("error", "Invalid: UNSAFE_PATH"),
        ("error", "Invalid: DB_CORRUPT"),
    ]

    fm = QFontMetrics(pill_font())
    for status, label in labels:
        pill = StatusPillWidget(status=status, label=label)
        adv = fm.horizontalAdvance(label)
        # Required: widget width >= text advance + horizontal padding (20 px)
        assert pill.width() >= adv + 20, f"Pill width {pill.width()} too small for '{label}' (advance={adv})"
