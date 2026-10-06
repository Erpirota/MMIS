"""Tests for SQLite persistence, migrations, and repository operations."""

import sqlite3
from pathlib import Path
import pytest

from mmis.domain.errors import (
    DuplicateProfileError,
    SchemaTooNewError,
    StorageError,
)
from mmis.domain.profiles import ScanSummary
from mmis.storage.db import connect_db
from mmis.storage.migrations import apply_migrations
from mmis.storage.repository import ProfileRepository


def test_schema_creation_and_idempotency(tmp_path: Path):
    """Fresh DB initializes at current schema version and reopening is idempotent."""
    db_file = tmp_path / "test.db"
    conn = connect_db(db_file)
    cur = conn.cursor()

    cur.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == 3

    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cur.fetchall()}
    assert "profiles" in tables
    assert "scans" in tables
    assert "crash_reports" in tables
    conn.close()

    # Reopening should succeed without re-applying migrations
    conn2 = connect_db(db_file)
    cur2 = conn2.cursor()
    cur2.execute("PRAGMA user_version;")
    assert cur2.fetchone()[0] == 3
    conn2.close()


def test_schema_too_new_error(tmp_path: Path):
    """Opening a DB with user_version > supported raises SchemaTooNewError without modifying DB."""
    db_file = tmp_path / "future.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA user_version = 999;")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNewError) as exc_info:
        connect_db(db_file)
    assert "is newer than supported" in str(exc_info.value)

    # Verify user_version is untouched
    conn_check = sqlite3.connect(str(db_file))
    assert conn_check.execute("PRAGMA user_version;").fetchone()[0] == 999
    conn_check.close()


def test_failed_migration_rollback(tmp_path: Path):
    """A failing migration rolls back completely and does not increment user_version."""
    db_file = tmp_path / "fail.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA user_version = 0;")
    conn.commit()

    # Create dummy migration that fails midway
    def bad_migration(c: sqlite3.Connection):
        c.execute("CREATE TABLE partial (id INT);")
        raise RuntimeError("Disk error simulation")

    from mmis.storage import migrations
    orig_migrations = list(migrations.MIGRATIONS)
    migrations.MIGRATIONS = [bad_migration]

    try:
        with pytest.raises(StorageError) as exc_info:
            apply_migrations(conn, str(db_file))
        assert "Migration to version 1 failed" in str(exc_info.value)
    finally:
        migrations.MIGRATIONS = orig_migrations

    # Verify rollback: partial table must not exist and user_version is 0
    cur = conn.cursor()
    cur.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='partial';")
    assert cur.fetchone() is None
    conn.close()


def test_corrupt_database_file(tmp_path: Path):
    """Opening a corrupt/non-sqlite file raises StorageError with the file path."""
    bad_file = tmp_path / "not_sqlite.db"
    bad_file.write_text("This is definitely not a sqlite database file!")

    with pytest.raises(StorageError) as exc_info:
        connect_db(bad_file)
    assert str(bad_file) in str(exc_info.value)


def test_profile_crud_and_foreign_keys(tmp_path: Path):
    """Profile CRUD, case-insensitive duplicate check, and cascade deletion."""
    repo = ProfileRepository(tmp_path / "repo.db")

    # Create
    p1 = repo.create_profile("Pack Alpha", "/path/to/alpha", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    assert p1.id is not None
    assert p1.name == "Pack Alpha"

    # Case-insensitive duplicate rejection
    with pytest.raises(DuplicateProfileError):
        repo.create_profile("pack alpha", "/other/path", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")

    # Get by name & id
    by_name = repo.get_profile_by_name("PACK ALPHA")
    assert by_name is not None
    assert by_name.id == p1.id
    assert by_name.name == "Pack Alpha"

    by_id = repo.get_profile_by_id(p1.id)
    assert by_id == by_name

    # List
    repo.create_profile("Beta Pack", "/path/to/beta", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    all_profiles = repo.list_profiles()
    assert len(all_profiles) == 2
    assert [p.name for p in all_profiles] == ["Beta Pack", "Pack Alpha"]

    # Rename
    repo.rename_profile(p1.id, "Pack Omega", "2026-01-02T00:00:00Z")
    renamed = repo.get_profile_by_id(p1.id)
    assert renamed.name == "Pack Omega"

    # Rename collision
    with pytest.raises(DuplicateProfileError):
        repo.rename_profile(p1.id, "beta pack", "2026-01-02T00:00:00Z")

    # Update path
    repo.update_profile_path(p1.id, "/new/path", "2026-01-03T00:00:00Z")
    assert repo.get_profile_by_id(p1.id).path == "/new/path"

    # Insert scans and test cascade deletion
    summary = ScanSummary(
        id=0,
        profile_id=p1.id,
        scanned_at="2026-01-03T00:00:00Z",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=10,
        mod_count=10,
        error_count=0,
        warning_count=0,
        info_count=0,
    )
    scan_id = repo.insert_scan(p1.id, summary, '{"mock": "payload"}')
    assert scan_id > 0
    assert repo.get_scan(scan_id) is not None

    # Delete profile cascades scan
    deleted = repo.delete_profile(p1.id)
    assert deleted is True
    assert repo.get_profile_by_id(p1.id) is None
    assert repo.get_scan(scan_id) is None


def test_retention_and_pinned_scans(tmp_path: Path):
    """Retention keeps newest N unpinned scans; pinned scans are never pruned."""
    repo = ProfileRepository(tmp_path / "retention.db")
    prof = repo.create_profile("RetentionPack", "/retention", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")

    scan_ids: list[int] = []
    # Insert 5 scans with retention limit 3
    for i in range(5):
        summary = ScanSummary(
            id=0,
            profile_id=prof.id,
            scanned_at=f"2026-01-0{i+1}T00:00:00Z",
            loader="fabric",
            mc_version="1.20.1",
            jar_count=1,
            mod_count=1,
            error_count=0,
            warning_count=0,
            info_count=0,
        )
        s_id = repo.insert_scan(prof.id, summary, '{"index": ' + str(i) + '}', retention_limit=3)
        scan_ids.append(s_id)

    # Scans 0 and 1 should have been pruned (5 inserted, retention 3 -> scans 2, 3, 4 kept)
    assert repo.get_scan(scan_ids[0]) is None
    assert repo.get_scan(scan_ids[1]) is None
    assert repo.get_scan(scan_ids[2]) is not None
    assert repo.get_scan(scan_ids[3]) is not None
    assert repo.get_scan(scan_ids[4]) is not None

    # Pin scan 2
    repo.set_scan_pinned(scan_ids[2], True)
    summary_2, _ = repo.get_scan(scan_ids[2])
    assert summary_2.pinned is True

    # Insert 3 more scans with retention limit 2
    for i in range(5, 8):
        summary = ScanSummary(
            id=0,
            profile_id=prof.id,
            scanned_at=f"2026-01-0{i+1}T00:00:00Z",
            loader="fabric",
            mc_version="1.20.1",
            jar_count=1,
            mod_count=1,
            error_count=0,
            warning_count=0,
            info_count=0,
        )
        s_id = repo.insert_scan(prof.id, summary, '{"index": ' + str(i) + '}', retention_limit=2)
        scan_ids.append(s_id)

    # Pinned scan 2 MUST still exist despite being older than retention window
    assert repo.get_scan(scan_ids[2]) is not None

    # Label persistence
    repo.set_scan_label(scan_ids[2], "ReleaseCandidate")
    labeled_summary, _ = repo.get_scan(scan_ids[2])
    assert labeled_summary.label == "ReleaseCandidate"
