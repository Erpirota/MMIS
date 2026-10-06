"""Database schema migrations managed via PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from typing import Callable

from mmis.domain.errors import SchemaTooNewError, StorageError


def _migration_v1(conn: sqlite3.Connection) -> None:
    """Create initial profiles and scans tables."""
    conn.executescript(
        """
        CREATE TABLE profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            scanned_at TEXT NOT NULL,
            loader TEXT,
            mc_version TEXT,
            jar_count INTEGER NOT NULL,
            mod_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            info_count INTEGER NOT NULL,
            label TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_scans_profile_id ON scans(profile_id);
        CREATE INDEX idx_scans_scanned_at ON scans(scanned_at);
        """
    )


def _migration_v2(conn: sqlite3.Connection) -> None:
    """Add analysis_json column to scans table."""
    conn.execute("ALTER TABLE scans ADD COLUMN analysis_json TEXT;")


def _migration_v3(conn: sqlite3.Connection) -> None:
    """Add crash_reports table."""
    conn.executescript(
        """
        CREATE TABLE crash_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
            imported_at TEXT NOT NULL,
            source_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            kind TEXT NOT NULL,
            mc_version TEXT,
            loader TEXT,
            top_suspect TEXT,
            error_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            info_count INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX idx_crash_reports_profile_id ON crash_reports(profile_id);
        CREATE INDEX idx_crash_reports_imported_at ON crash_reports(imported_at);
        """
    )


# Ordered list of sequential migrations: index 0 is version 1, etc.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_v1,
    _migration_v2,
    _migration_v3,
]


def apply_migrations(conn: sqlite3.Connection, db_path: str = "") -> None:
    """Check schema version and apply any pending migrations in transactions."""
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        row = cursor.fetchone()
        current_version = row[0] if row else 0
    except Exception as exc:
        raise StorageError(f"Failed to read schema version from database '{db_path}': {exc}") from exc

    target_version = len(MIGRATIONS)

    if current_version > target_version:
        raise SchemaTooNewError(
            f"Database '{db_path}' schema version ({current_version}) is newer than supported ({target_version})."
        )

    if current_version == target_version:
        return

    # Apply migrations sequentially
    for step in range(current_version, target_version):
        migration_fn = MIGRATIONS[step]
        new_version = step + 1
        conn.execute("BEGIN IMMEDIATE;")
        try:
            migration_fn(conn)
            conn.execute(f"PRAGMA user_version = {new_version};")
            conn.execute("COMMIT;")
        except Exception as exc:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise StorageError(
                f"Migration to version {new_version} failed on database '{db_path}': {exc}"
            ) from exc
