"""Repository for persistent storage of profiles and scan snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Generator

from mmis.domain.crash import CrashRecord
from mmis.domain.errors import DuplicateProfileError, StorageError
from mmis.domain.profiles import Profile, ScanSummary
from mmis.storage.db import connect_db


class ScanRow(tuple):
    """2-tuple (summary, payload_json) with backward-compatible analysis_json property."""

    def __new__(cls, summary: ScanSummary, payload_json: str, analysis_json: str | None = None):
        instance = super().__new__(cls, (summary, payload_json))
        instance._summary = summary
        instance._payload_json = payload_json
        instance._analysis_json = analysis_json
        return instance

    @property
    def summary(self) -> ScanSummary:
        return self._summary

    @property
    def payload_json(self) -> str:
        return self._payload_json

    @property
    def analysis_json(self) -> str | None:
        return self._analysis_json


class ProfileRepository:
    """Thread-safe SQLite repository.

    Opens a short-lived connection per unit of work with transactions.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = connect_db(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = connect_db(self.db_path)
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def create_profile(
        self,
        name: str,
        path: str,
        created_at: str,
        updated_at: str,
    ) -> Profile:
        """Create a new profile record."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO profiles (name, path, created_at, updated_at)
                    VALUES (?, ?, ?, ?);
                    """,
                    (name, path, created_at, updated_at),
                )
                profile_id = cursor.lastrowid
                return Profile(
                    id=profile_id,
                    name=name,
                    path=path,
                    created_at=created_at,
                    updated_at=updated_at,
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper() or "PROFILES.NAME" in str(exc).upper():
                raise DuplicateProfileError(f"A profile named '{name}' already exists.") from exc
            raise StorageError(f"Storage integrity error creating profile '{name}': {exc}") from exc
        except Exception as exc:
            raise StorageError(f"Storage error creating profile '{name}': {exc}") from exc

    def get_profile_by_name(self, name: str) -> Profile | None:
        """Find a profile by case-insensitive name."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT id, name, path, created_at, updated_at
                    FROM profiles
                    WHERE name = ? COLLATE NOCASE;
                    """,
                    (name,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return Profile(
                    id=row["id"],
                    name=row["name"],
                    path=row["path"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            except Exception as exc:
                raise StorageError(f"Storage error querying profile '{name}': {exc}") from exc

    def get_profile_by_id(self, profile_id: int) -> Profile | None:
        """Find a profile by integer ID."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT id, name, path, created_at, updated_at
                    FROM profiles
                    WHERE id = ?;
                    """,
                    (profile_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return Profile(
                    id=row["id"],
                    name=row["name"],
                    path=row["path"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            except Exception as exc:
                raise StorageError(f"Storage error querying profile ID {profile_id}: {exc}") from exc

    def list_profiles(self) -> list[Profile]:
        """List all profiles ordered alphabetically by case-insensitive name."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT id, name, path, created_at, updated_at
                    FROM profiles
                    ORDER BY name COLLATE NOCASE ASC;
                    """
                )
                return [
                    Profile(
                        id=row["id"],
                        name=row["name"],
                        path=row["path"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    for row in cursor.fetchall()
                ]
            except Exception as exc:
                raise StorageError(f"Storage error listing profiles: {exc}") from exc

    def update_profile_path(self, profile_id: int, new_path: str, updated_at: str) -> None:
        """Update the filesystem directory path for a profile."""
        try:
            with self._transaction() as conn:
                conn.execute(
                    """
                    UPDATE profiles
                    SET path = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (new_path, updated_at, profile_id),
                )
        except Exception as exc:
            raise StorageError(f"Storage error updating profile path for ID {profile_id}: {exc}") from exc

    def rename_profile(self, profile_id: int, new_name: str, updated_at: str) -> None:
        """Rename a profile."""
        try:
            with self._transaction() as conn:
                conn.execute(
                    """
                    UPDATE profiles
                    SET name = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (new_name, updated_at, profile_id),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper() or "PROFILES.NAME" in str(exc).upper():
                raise DuplicateProfileError(f"A profile named '{new_name}' already exists.") from exc
            raise StorageError(f"Storage integrity error renaming profile: {exc}") from exc
        except Exception as exc:
            raise StorageError(f"Storage error renaming profile ID {profile_id}: {exc}") from exc

    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile and cascade-delete all its scans."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM profiles WHERE id = ?;",
                    (profile_id,),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise StorageError(f"Storage error deleting profile ID {profile_id}: {exc}") from exc

    def insert_scan(
        self,
        profile_id: int,
        summary: ScanSummary,
        payload_json: str,
        analysis_json: str | None = None,
        retention_limit: int = 20,
    ) -> int:
        """Insert a scan record and prune unpinned scans exceeding retention_limit in a single transaction."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO scans (
                        profile_id, scanned_at, loader, mc_version,
                        jar_count, mod_count, error_count, warning_count,
                        info_count, label, pinned, payload_json, analysis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        profile_id,
                        summary.scanned_at,
                        summary.loader,
                        summary.mc_version,
                        summary.jar_count,
                        summary.mod_count,
                        summary.error_count,
                        summary.warning_count,
                        summary.info_count,
                        summary.label,
                        1 if summary.pinned else 0,
                        payload_json,
                        analysis_json,
                    ),
                )
                scan_id = cursor.lastrowid

                # Retention pruning: keep newest retention_limit unpinned scans
                if retention_limit > 0:
                    cursor = conn.execute(
                        """
                        SELECT id FROM scans
                        WHERE profile_id = ? AND pinned = 0
                        ORDER BY scanned_at DESC, id DESC;
                        """,
                        (profile_id,),
                    )
                    unpinned_ids = [row["id"] for row in cursor.fetchall()]
                    if len(unpinned_ids) > retention_limit:
                        to_delete = unpinned_ids[retention_limit:]
                        placeholders = ",".join("?" for _ in to_delete)
                        conn.execute(
                            f"DELETE FROM scans WHERE id IN ({placeholders});",
                            to_delete,
                        )

                return scan_id
        except Exception as exc:
            raise StorageError(f"Storage error inserting scan for profile ID {profile_id}: {exc}") from exc

    def get_scan(self, scan_id: int) -> ScanRow | None:
        """Retrieve a scan summary, payload_json, and analysis_json by scan ID."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT id, profile_id, scanned_at, loader, mc_version,
                           jar_count, mod_count, error_count, warning_count,
                           info_count, label, pinned, payload_json, analysis_json
                    FROM scans
                    WHERE id = ?;
                    """,
                    (scan_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                summary = ScanSummary(
                    id=row["id"],
                    profile_id=row["profile_id"],
                    scanned_at=row["scanned_at"],
                    loader=row["loader"],
                    mc_version=row["mc_version"],
                    jar_count=row["jar_count"],
                    mod_count=row["mod_count"],
                    error_count=row["error_count"],
                    warning_count=row["warning_count"],
                    info_count=row["info_count"],
                    label=row["label"],
                    pinned=bool(row["pinned"]),
                )
                analysis_json = row["analysis_json"] if "analysis_json" in row.keys() else None
                return ScanRow(summary, row["payload_json"], analysis_json)
            except Exception as exc:
                raise StorageError(f"Storage error reading scan ID {scan_id}: {exc}") from exc

    def insert_crash_report(
        self,
        profile_id: int | None,
        imported_at: str,
        source_name: str,
        file_size: int,
        sha256: str,
        kind: str,
        mc_version: str | None,
        loader: str | None,
        top_suspect: str | None,
        error_count: int,
        warning_count: int,
        info_count: int,
        payload_json: str,
    ) -> int:
        """Insert a crash report record and return its generated ID."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO crash_reports (
                        profile_id, imported_at, source_name, file_size, sha256,
                        kind, mc_version, loader, top_suspect,
                        error_count, warning_count, info_count, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        profile_id,
                        imported_at,
                        source_name,
                        file_size,
                        sha256,
                        kind,
                        mc_version,
                        loader,
                        top_suspect,
                        error_count,
                        warning_count,
                        info_count,
                        payload_json,
                    ),
                )
                return cursor.lastrowid
        except Exception as exc:
            raise StorageError(f"Storage error inserting crash report: {exc}") from exc

    def get_crash_report(self, crash_id: int) -> CrashRecord | None:
        """Retrieve a crash report record including payload_json."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT id, profile_id, imported_at, source_name, file_size, sha256,
                           kind, mc_version, loader, top_suspect,
                           error_count, warning_count, info_count, payload_json
                    FROM crash_reports
                    WHERE id = ?;
                    """,
                    (crash_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return CrashRecord(
                    id=row["id"],
                    profile_id=row["profile_id"],
                    imported_at=row["imported_at"],
                    source_name=row["source_name"],
                    file_size=row["file_size"],
                    sha256=row["sha256"],
                    kind=row["kind"],
                    mc_version=row["mc_version"],
                    loader=row["loader"],
                    top_suspect=row["top_suspect"],
                    error_count=row["error_count"],
                    warning_count=row["warning_count"],
                    info_count=row["info_count"],
                    payload_json=row["payload_json"],
                )
            except Exception as exc:
                raise StorageError(f"Storage error reading crash report ID {crash_id}: {exc}") from exc

    def list_crash_reports(self, profile_id: int | None = None, limit: int | None = None) -> list[CrashRecord]:
        """List crash report summaries, optionally filtered by profile_id."""
        with self._connection() as conn:
            try:
                query = """
                    SELECT id, profile_id, imported_at, source_name, file_size, sha256,
                           kind, mc_version, loader, top_suspect,
                           error_count, warning_count, info_count
                    FROM crash_reports
                """
                params: list[Any] = []
                if profile_id is not None:
                    query += " WHERE profile_id = ?"
                    params.append(profile_id)
                query += " ORDER BY imported_at DESC, id DESC"
                if limit is not None and limit > 0:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor = conn.execute(query, params)
                return [
                    CrashRecord(
                        id=row["id"],
                        profile_id=row["profile_id"],
                        imported_at=row["imported_at"],
                        source_name=row["source_name"],
                        file_size=row["file_size"],
                        sha256=row["sha256"],
                        kind=row["kind"],
                        mc_version=row["mc_version"],
                        loader=row["loader"],
                        top_suspect=row["top_suspect"],
                        error_count=row["error_count"],
                        warning_count=row["warning_count"],
                        info_count=row["info_count"],
                    )
                    for row in cursor.fetchall()
                ]
            except Exception as exc:
                raise StorageError(f"Storage error listing crash reports: {exc}") from exc

    def delete_crash_report(self, crash_id: int) -> bool:
        """Delete a crash report by ID."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute("DELETE FROM crash_reports WHERE id = ?;", (crash_id,))
                return cursor.rowcount > 0
        except Exception as exc:
            raise StorageError(f"Storage error deleting crash report ID {crash_id}: {exc}") from exc

    def list_scans_for_profile(self, profile_id: int, limit: int | None = None) -> list[ScanSummary]:
        """List scan summaries for a profile, ordered newest first."""
        with self._connection() as conn:
            try:
                query = """
                    SELECT id, profile_id, scanned_at, loader, mc_version,
                           jar_count, mod_count, error_count, warning_count,
                           info_count, label, pinned
                    FROM scans
                    WHERE profile_id = ?
                    ORDER BY scanned_at DESC, id DESC
                """
                params: list[int] = [profile_id]
                if limit is not None and limit > 0:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor = conn.execute(query, params)
                return [
                    ScanSummary(
                        id=row["id"],
                        profile_id=row["profile_id"],
                        scanned_at=row["scanned_at"],
                        loader=row["loader"],
                        mc_version=row["mc_version"],
                        jar_count=row["jar_count"],
                        mod_count=row["mod_count"],
                        error_count=row["error_count"],
                        warning_count=row["warning_count"],
                        info_count=row["info_count"],
                        label=row["label"],
                        pinned=bool(row["pinned"]),
                    )
                    for row in cursor.fetchall()
                ]
            except Exception as exc:
                raise StorageError(f"Storage error listing scans for profile ID {profile_id}: {exc}") from exc

    def get_latest_scan_for_profile(self, profile_id: int) -> ScanSummary | None:
        """Get the most recent scan summary for a profile."""
        scans = self.list_scans_for_profile(profile_id, limit=1)
        return scans[0] if scans else None

    def get_scan_count_for_profile(self, profile_id: int) -> int:
        """Count total stored scans for a profile."""
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    "SELECT COUNT(*) AS total FROM scans WHERE profile_id = ?;",
                    (profile_id,),
                )
                row = cursor.fetchone()
                return row["total"] if row else 0
            except Exception as exc:
                raise StorageError(f"Storage error counting scans for profile ID {profile_id}: {exc}") from exc

    def set_scan_label(self, scan_id: int, label: str | None) -> bool:
        """Set or clear a scan's descriptive label."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "UPDATE scans SET label = ? WHERE id = ?;",
                    (label, scan_id),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise StorageError(f"Storage error setting label for scan ID {scan_id}: {exc}") from exc

    def set_scan_pinned(self, scan_id: int, pinned: bool) -> bool:
        """Set or clear a scan's pinned flag."""
        try:
            with self._transaction() as conn:
                cursor = conn.execute(
                    "UPDATE scans SET pinned = ? WHERE id = ?;",
                    (1 if pinned else 0, scan_id),
                )
                return cursor.rowcount > 0
        except Exception as exc:
            raise StorageError(f"Storage error updating pinned for scan ID {scan_id}: {exc}") from exc
