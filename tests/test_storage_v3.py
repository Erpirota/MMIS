"""Tests for storage migrations v1 -> v3, crash report CRUD, and scan analysis integration."""

import json
from pathlib import Path
import sqlite3
import pytest

from mmis.analysis.engine import analyze_pack
from mmis.domain.models import JarFile, Loader, ModInfo, ScanResult
from mmis.services.profiles import ProfileService
from mmis.storage.db import connect_db
from mmis.storage.migrations import _migration_v1
from mmis.storage.repository import ProfileRepository


def test_v1_to_v3_migration_real_file(tmp_path: Path):
    """Create a real SQLite database at v1, insert rows, upgrade to v3, verify integrity and recomputation."""
    db_file = tmp_path / "v1_legacy.db"

    # 1. Create real v1 database manually
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA user_version = 1;")
    _migration_v1(conn)

    # Insert a v1 profile
    cursor = conn.execute(
        "INSERT INTO profiles (name, path, created_at, updated_at) VALUES ('Old Pack', '/path/to/old', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');"
    )
    prof_id = cursor.lastrowid

    # Create a ScanResult and serialize as v1 payload_json
    mod = ModInfo(id="legacy_mod", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/legacy.jar")
    jar = JarFile(path="mods/legacy.jar", size_bytes=100, sha256="abc", mods=["legacy_mod"])
    scan_res = ScanResult(scanned_path="/path/to/old", mods_dir="mods", jars=[jar], mods=[mod], detected_loader=Loader.FABRIC)
    payload_json = json.dumps(scan_res.to_dict())

    # Insert a v1 scan (no analysis_json column exists in v1!)
    cursor = conn.execute(
        """
        INSERT INTO scans (
            profile_id, scanned_at, loader, mc_version, jar_count, mod_count,
            error_count, warning_count, info_count, label, pinned, payload_json
        ) VALUES (?, '2026-01-01T00:00:00Z', 'fabric', '1.20.1', 1, 1, 0, 0, 0, 'v1 scan', 0, ?);
        """,
        (prof_id, payload_json),
    )
    scan_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # 2. Open via ProfileRepository -> triggers automatic migration to v3
    repo = ProfileRepository(db_file)
    service = ProfileService(repo)

    # Verify user_version is 3
    with repo._connection() as c:
        v = c.execute("PRAGMA user_version;").fetchone()[0]
        assert v == 3

        # Verify crash_reports table exists
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        assert "crash_reports" in tables

    # Verify profile and scan exist and are intact
    prof = service.get_profile("Old Pack")
    assert prof.id == prof_id
    assert prof.name == "Old Pack"

    # get_scan reads the scan correctly
    loaded_scan = service.get_scan("Old Pack", scan_id)
    assert loaded_scan.mods[0].id == "legacy_mod"

    # get_scan_record recomputes analysis on-the-fly for old row
    rec = service.get_scan_record("Old Pack", scan_id)
    assert rec.summary.id == scan_id
    assert rec.analysis is not None
    assert isinstance(rec.analysis.severity_counts, dict)


def test_crash_crud_and_profile_cascade_set_null(tmp_path: Path):
    """Test crash report CRUD and ON DELETE SET NULL on profile deletion."""
    db_file = tmp_path / "crash_test.db"
    repo = ProfileRepository(db_file)
    service = ProfileService(repo)

    # Create profile
    prof = service.add_profile("CrashProfile", tmp_path)

    # Insert crash report bound to profile
    log_content = (
        "java.lang.NullPointerException: Error\n"
        "\tat com.example.MyMod.tick(MyMod.java:50)\n"
    )
    crash_rec = service.import_crash(log_content, profile_name="CrashProfile", source_name="crash1.txt")
    assert crash_rec.id > 0
    assert crash_rec.profile_id == prof.id
    assert crash_rec.source_name == "crash1.txt"

    # List crashes
    all_crashes = service.list_crashes()
    assert len(all_crashes) == 1
    assert all_crashes[0].id == crash_rec.id

    prof_crashes = service.list_crashes(profile_name="CrashProfile")
    assert len(prof_crashes) == 1

    # Get crash
    rec, analysis = service.get_crash(crash_rec.id)
    assert rec.id == crash_rec.id
    assert analysis.root_cause is not None
    assert analysis.root_cause.exception_class == "java.lang.NullPointerException"

    # Delete profile -> verify crash report profile_id is set to NULL (ON DELETE SET NULL)
    deleted = service.remove_profile("CrashProfile")
    assert deleted is True

    # Crash report must still exist with profile_id = None
    rec_after, _ = service.get_crash(crash_rec.id)
    assert rec_after is not None
    assert rec_after.profile_id is None

    # Delete crash report
    del_res = service.delete_crash(crash_rec.id)
    assert del_res is True

    with pytest.raises(ValueError):
        service.get_crash(crash_rec.id)
