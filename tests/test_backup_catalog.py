"""Unit tests for mmis.services.backup_catalog."""

from __future__ import annotations

import json
from pathlib import Path
import time
import zipfile

import pytest

from mmis.services import backup_catalog
from mmis.services.backup_catalog import BackupEntry, backups_dir, list_backups, new_backup_path


def test_backups_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    b_dir = backups_dir(data_dir)
    assert b_dir == data_dir / "backups"
    assert isinstance(b_dir, Path)


def test_new_backup_path_naming_and_collision(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    b_dir = backups_dir(data_dir)
    b_dir.mkdir(parents=True)

    p1 = new_backup_path(data_dir)
    assert p1.parent == b_dir
    assert p1.name.startswith("mmis_backup_")
    assert p1.suffix == ".zip"

    # Create p1 to simulate existing file with the same timestamp
    p1.touch()
    p2 = new_backup_path(data_dir)
    assert p2 != p1
    assert p2.name.endswith("_1.zip")

    p2.touch()
    p3 = new_backup_path(data_dir)
    assert p3 != p2
    assert p3.name.endswith("_2.zip")


def test_list_backups_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    entries = list_backups(data_dir)
    assert entries == []


def test_list_backups_discovery_and_ordering(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    b_dir = backups_dir(data_dir)
    b_dir.mkdir(parents=True)

    # 1. Manual backup in backups_dir
    man_path = b_dir / "mmis_backup_20260101_120000.zip"
    with zipfile.ZipFile(man_path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app_version": "1.0", "schema_version": 3, "created_at": "2026-01-01T12:00:00+00:00"}),
        )
        zf.writestr("mmis.db", b"dummy db")

    # 2. Pre-restore safety backup directly in data_dir
    safety_path = data_dir / "mmis_pre_restore_20260102_120000.zip"
    with zipfile.ZipFile(safety_path, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"app_version": "1.0", "schema_version": 3, "created_at": "2026-01-02T12:00:00+00:00"}),
        )
        zf.writestr("mmis.db", b"safety db")

    # Ensure mtime of safety is newer than manual
    import os
    os.utime(man_path, (1000000, 1000000))
    os.utime(safety_path, (2000000, 2000000))

    entries = list_backups(data_dir)
    assert len(entries) == 2

    # Newest mtime first
    assert entries[0].path == safety_path
    assert entries[0].kind == "pre_restore"
    assert entries[0].manifest_ok is True
    assert entries[0].schema_version == 3

    assert entries[1].path == man_path
    assert entries[1].kind == "manual"
    assert entries[1].manifest_ok is True
    assert entries[1].app_version == "1.0"


def test_list_backups_corrupted_archive(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    b_dir = backups_dir(data_dir)
    b_dir.mkdir(parents=True)

    bad_zip = b_dir / "corrupted.zip"
    bad_zip.write_bytes(b"not a valid zip")

    entries = list_backups(data_dir)
    assert len(entries) == 1
    assert entries[0].path == bad_zip
    assert entries[0].manifest_ok is False
    assert entries[0].error is not None
