"""Unit and integration tests for backup creation, verification, and restoration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import zipfile

import pytest

from mmis.domain.errors import BackupInvalidError, ExportTargetExistsError
from mmis.services.backup import (
    CURRENT_SCHEMA_VERSION,
    create_backup,
    restore_backup,
    verify_backup,
)
from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.storage.repository import ProfileRepository


def _sha256_dir(d: Path) -> dict[str, str]:
    hashes = {}
    for p in sorted(d.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            hashes[p.relative_to(d).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def _setup_demo_data(data_dir: Path) -> ProfileService:
    db_path = get_db_path(data_dir)
    repo = ProfileRepository(db_path)
    service = ProfileService(repo)

    # Add profile & dummy crash
    p = service.add_profile("TestPack", data_dir / "pack")
    service.import_crash(b"Dummy crash trace", profile_name="TestPack", source_name="crash.txt")

    # Add settings.json
    settings_file = data_dir / "app_settings.json"
    settings_file.write_text('{"animations_enabled": true}', encoding="utf-8")
    return service


def test_backup_create_verify_and_restore(tmp_path: Path):
    src_data = tmp_path / "src_data"
    dst_data = tmp_path / "dst_data"
    backup_zip = tmp_path / "backup.zip"

    service = _setup_demo_data(src_data)

    # 1. Test live backup while another connection is open
    db_path = get_db_path(src_data)
    live_conn = sqlite3.connect(str(db_path))
    live_conn.execute("SELECT count(*) FROM profiles;")

    created_path = create_backup(data_dir=src_data, out_path=backup_zip)
    live_conn.close()

    assert created_path.is_file()
    assert created_path == backup_zip.resolve()

    # 2. Verify backup archive
    manifest = verify_backup(backup_zip)
    assert manifest["schema_version"] == CURRENT_SCHEMA_VERSION
    assert "mmis.db" in manifest["files"]
    assert "settings.json" in manifest["files"]

    # 3. Restore into empty data directory
    dst_data.mkdir(parents=True, exist_ok=True)
    safety_backup = restore_backup(zip_path=backup_zip, data_dir=dst_data, force=True)

    # Verify restored data
    dst_repo = ProfileRepository(get_db_path(dst_data))
    dst_service = ProfileService(dst_repo)
    p = dst_service.get_profile("TestPack")
    assert p.name == "TestPack"
    crashes = dst_service.list_crashes("TestPack")
    assert len(crashes) == 1

    # Safety backup was created and is valid
    assert safety_backup.is_file()
    safety_manifest = verify_backup(safety_backup)
    assert safety_manifest["schema_version"] == CURRENT_SCHEMA_VERSION


def test_backup_create_refuses_overwrite_without_force(tmp_path: Path):
    src_data = tmp_path / "src_data"
    _setup_demo_data(src_data)
    backup_zip = tmp_path / "backup.zip"
    backup_zip.write_text("existing", encoding="utf-8")

    with pytest.raises(ExportTargetExistsError):
        create_backup(data_dir=src_data, out_path=backup_zip, force=False)

    # Succeeds with force=True
    create_backup(data_dir=src_data, out_path=backup_zip, force=True)
    assert zipfile.is_zipfile(backup_zip)


@pytest.mark.parametrize(
    "tamper_action, expected_reason",
    [
        ("missing_manifest", "MISSING_MANIFEST"),
        ("hash_mismatch", "HASH_MISMATCH"),
        ("unexpected_member", "UNEXPECTED_MEMBER"),
        ("duplicate_member", "UNEXPECTED_MEMBER"),
        ("unsafe_path_dotdot", "UNSAFE_PATH"),
        ("unsafe_path_abs", "UNSAFE_PATH"),
        ("unsafe_path_backslash", "UNSAFE_PATH"),
        ("too_large", "TOO_LARGE"),
        ("db_corrupt", "DB_CORRUPT"),
        ("schema_too_new_manifest", "SCHEMA_TOO_NEW"),
        ("schema_too_new_db", "SCHEMA_TOO_NEW"),
    ],
)
def test_restore_refusal_reasons_and_data_integrity(tmp_path: Path, tamper_action: str, expected_reason: str):
    src_data = tmp_path / "src_data"
    _setup_demo_data(src_data)
    valid_zip = tmp_path / "valid.zip"
    create_backup(data_dir=src_data, out_path=valid_zip)

    tampered_zip = tmp_path / f"tampered_{tamper_action}.zip"

    with zipfile.ZipFile(valid_zip, "r") as z_in:
        infolist = z_in.infolist()
        manifest = json.loads(z_in.read("manifest.json"))
        db_bytes = z_in.read("mmis.db")
        settings_bytes = z_in.read("settings.json")

    with zipfile.ZipFile(tampered_zip, "w") as z_out:
        if tamper_action == "missing_manifest":
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "hash_mismatch":
            manifest["files"]["mmis.db"] = "0000000000000000000000000000000000000000000000000000000000000000"
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "unexpected_member":
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)
            z_out.writestr("evil.sh", "echo evil")

        elif tamper_action == "duplicate_member":
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "unsafe_path_dotdot":
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("../mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "unsafe_path_abs":
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("/tmp/mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "unsafe_path_backslash":
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("sub\\mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "too_large":
            # Create a settings.json member with uncompressed size > 5 MB
            big_settings = b"A" * (6 * 1024 * 1024)
            manifest["files"]["settings.json"] = hashlib.sha256(big_settings).hexdigest()
            # Write manifest with matching hash so it fails on TOO_LARGE before extraction
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", big_settings)

        elif tamper_action == "db_corrupt":
            corrupt_db = b"THIS IS NOT A SQLITE DATABASE FILE"
            manifest["files"]["mmis.db"] = hashlib.sha256(corrupt_db).hexdigest()
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", corrupt_db)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "schema_too_new_manifest":
            manifest["schema_version"] = 99
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", db_bytes)
            z_out.writestr("settings.json", settings_bytes)

        elif tamper_action == "schema_too_new_db":
            # Modify user_version in the db
            tmp_db = tmp_path / "newer_db.db"
            tmp_db.write_bytes(db_bytes)
            conn = sqlite3.connect(str(tmp_db))
            conn.execute("PRAGMA user_version = 99;")
            conn.close()
            new_db_bytes = tmp_db.read_bytes()
            manifest["files"]["mmis.db"] = hashlib.sha256(new_db_bytes).hexdigest()
            z_out.writestr("manifest.json", json.dumps(manifest))
            z_out.writestr("mmis.db", new_db_bytes)
            z_out.writestr("settings.json", settings_bytes)

    # Setup target data directory and record initial hash
    target_data = tmp_path / f"target_data_{tamper_action}"
    _setup_demo_data(target_data)
    initial_hashes = _sha256_dir(target_data)

    with pytest.raises(BackupInvalidError) as exc_info:
        restore_backup(zip_path=tampered_zip, data_dir=target_data, force=True)

    assert exc_info.value.reason == expected_reason

    # Data directory must remain byte-identical
    post_hashes = _sha256_dir(target_data)
    assert post_hashes == initial_hashes

    # No temporary files or restore directories left behind
    leftover_tmps = list(target_data.glob(".mmis_restore_tmp_*"))
    assert len(leftover_tmps) == 0
