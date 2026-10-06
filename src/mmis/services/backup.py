"""Backup, verification, and restoration services for MMIS.

Ensures cryptographically verified archives (SQLite backup API + settings + manifest),
strict pre-validation before touch, safety backups, and atomic swaps.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Callable
import zipfile

from mmis.domain.errors import BackupInvalidError, ExportTargetExistsError
from mmis.services.paths import get_db_path, resolve_data_dir
from mmis.services.settings import SETTINGS_FILENAME, get_settings_path

APP_VERSION = "0.1.0"
CURRENT_SCHEMA_VERSION = 3
MEMBER_WHITELIST = frozenset({"manifest.json", "mmis.db", "settings.json"})

MAX_DB_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_OTHER_SIZE = 5 * 1024 * 1024       # 5 MB


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _is_symlink(zinfo: zipfile.ZipInfo) -> bool:
    mode = zinfo.external_attr >> 16
    return (mode & 0o170000) == 0o120000


def create_backup(
    data_dir: Path | str | None = None,
    out_path: Path | str = "",
    force: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    """Create a validated backup archive containing mmis.db, settings.json, and manifest.json.

    Args:
        data_dir: Source data directory (defaults to platform data dir).
        out_path: Target destination path for .zip backup archive.
        force: If True, overwrite existing target file.
        clock: Optional time provider for created_at manifest field.

    Returns:
        Resolved Path to created backup archive.
    """
    resolved_data = resolve_data_dir(data_dir)
    target_out = Path(out_path).resolve()

    if target_out.exists() and not force:
        raise ExportTargetExistsError(f"Target backup file '{target_out}' already exists. Use --force to overwrite.")

    target_out.parent.mkdir(parents=True, exist_ok=True)
    temp_zip = target_out.with_suffix(f".tmp_{os.getpid()}")

    src_db = get_db_path(resolved_data)
    src_settings = get_settings_path(resolved_data)
    if not src_settings.is_file():
        alt_settings = resolved_data / "settings.json"
        if alt_settings.is_file():
            src_settings = alt_settings

    now_fn = clock or (lambda: datetime.now(timezone.utc))
    created_at = now_fn().isoformat()

    manifest_files: dict[str, str] = {}

    with tempfile.TemporaryDirectory(dir=resolved_data) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        tmp_db = tmp_dir / "mmis.db"

        # Consistent live SQLite backup using short-lived connection
        if src_db.exists():
            src_conn = sqlite3.connect(f"file:{src_db.resolve().as_posix()}?mode=ro", uri=True)
            try:
                dst_conn = sqlite3.connect(str(tmp_db))
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()
        else:
            # Create an empty, properly migrated DB if none exists
            from mmis.storage.db import connect_db
            conn = connect_db(tmp_db)
            conn.close()

        manifest_files["mmis.db"] = _sha256_file(tmp_db)

        # Check DB user_version
        chk_conn = sqlite3.connect(f"file:{tmp_db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            cur = chk_conn.cursor()
            cur.execute("PRAGMA user_version;")
            row = cur.fetchone()
            db_schema_ver = row[0] if row else CURRENT_SCHEMA_VERSION
        finally:
            chk_conn.close()

        # Handle settings.json
        tmp_settings = tmp_dir / "settings.json"
        has_settings = False
        if src_settings.is_file():
            shutil.copy2(src_settings, tmp_settings)
            manifest_files["settings.json"] = _sha256_file(tmp_settings)
            has_settings = True

        manifest_data = {
            "app_version": APP_VERSION,
            "created_at": created_at,
            "files": manifest_files,
            "schema_version": db_schema_ver,
        }
        manifest_json_bytes = json.dumps(manifest_data, indent=2, sort_keys=True).encode("utf-8")

        try:
            with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", manifest_json_bytes)
                zf.write(tmp_db, arcname="mmis.db")
                if has_settings:
                    zf.write(tmp_settings, arcname="settings.json")

            temp_zip.replace(target_out)
            return target_out
        except Exception:
            if temp_zip.exists():
                temp_zip.unlink(missing_ok=True)
            raise


def verify_backup(zip_path: Path | str) -> dict[str, Any]:
    """Inspect and cryptographically verify a backup archive before extracting.

    Raises:
        BackupInvalidError: With typed reason codes on failure.
    """
    path_obj = Path(zip_path).resolve()
    if not path_obj.is_file():
        raise BackupInvalidError("MISSING_MANIFEST", f"Backup file '{path_obj}' does not exist.")

    if not zipfile.is_zipfile(path_obj):
        raise BackupInvalidError("DB_CORRUPT", f"File '{path_obj}' is not a valid ZIP archive.")

    try:
        with zipfile.ZipFile(path_obj, "r") as zf:
            infolist = zf.infolist()
            namelist = [info.filename for info in infolist]

            # 1. Check duplicate member names
            if len(namelist) != len(set(namelist)):
                raise BackupInvalidError("UNEXPECTED_MEMBER", "Backup archive contains duplicate member names.")

            # 2. Check path safety and whitelist
            for info in infolist:
                fn = info.filename
                # Path traversal, backslashes, slashes, absolute paths
                if "\\" in fn or "/" in fn or ":" in fn:
                    raise BackupInvalidError("UNSAFE_PATH", f"Unsafe member path '{fn}'.")
                parts = fn.split("/")
                if ".." in parts or "." in parts:
                    raise BackupInvalidError("UNSAFE_PATH", f"Unsafe traversal in member path '{fn}'.")

                if _is_symlink(info):
                    raise BackupInvalidError("UNSAFE_PATH", f"Symlink member '{fn}' is forbidden.")

                if info.is_dir():
                    raise BackupInvalidError("UNEXPECTED_MEMBER", f"Unexpected directory entry '{fn}'.")

                if fn not in MEMBER_WHITELIST:
                    raise BackupInvalidError("UNEXPECTED_MEMBER", f"Unexpected member '{fn}' not in whitelist.")

                # Member size limits
                if fn == "mmis.db" and info.file_size > MAX_DB_SIZE:
                    raise BackupInvalidError("TOO_LARGE", f"Database size ({info.file_size} bytes) exceeds limit.")
                elif fn != "mmis.db" and info.file_size > MAX_OTHER_SIZE:
                    raise BackupInvalidError("TOO_LARGE", f"Member '{fn}' size ({info.file_size} bytes) exceeds limit.")

            # 3. Check manifest exists
            if "manifest.json" not in namelist:
                raise BackupInvalidError("MISSING_MANIFEST", "Archive is missing manifest.json.")

            try:
                manifest_bytes = zf.read("manifest.json")
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except Exception as exc:
                raise BackupInvalidError("MISSING_MANIFEST", f"Failed to parse manifest.json: {exc}") from exc

            if not isinstance(manifest, dict) or "files" not in manifest or "schema_version" not in manifest:
                raise BackupInvalidError("MISSING_MANIFEST", "Manifest missing required schema_version or files list.")

            schema_ver = manifest["schema_version"]
            if not isinstance(schema_ver, int) or schema_ver > CURRENT_SCHEMA_VERSION:
                raise BackupInvalidError("SCHEMA_TOO_NEW", f"Backup schema version ({schema_ver}) exceeds current ({CURRENT_SCHEMA_VERSION}).")

            files_map = manifest["files"]
            if not isinstance(files_map, dict) or "mmis.db" not in files_map:
                raise BackupInvalidError("MISSING_MANIFEST", "Manifest files mapping missing mmis.db.")

            # 4. Verify SHA-256 for all members
            for member_name, expected_hash in files_map.items():
                if member_name not in namelist:
                    raise BackupInvalidError("HASH_MISMATCH", f"Manifest references missing member '{member_name}'.")
                actual_bytes = zf.read(member_name)
                actual_hash = _sha256_bytes(actual_bytes)
                if actual_hash.lower() != str(expected_hash).lower():
                    raise BackupInvalidError("HASH_MISMATCH", f"SHA-256 mismatch for member '{member_name}'.")

            # 5. Verify extracted DB integrity
            db_bytes = zf.read("mmis.db")
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_f:
                tmp_f.write(db_bytes)
                tmp_db_path = Path(tmp_f.name)

            conn = None
            try:
                conn = sqlite3.connect(f"file:{tmp_db_path.resolve().as_posix()}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                rows = cur.fetchall()
                if not rows or rows[0][0] != "ok":
                    raise BackupInvalidError("DB_CORRUPT", f"Integrity check failed: {rows}")

                cur.execute("PRAGMA user_version;")
                db_uv = cur.fetchone()[0]
                if db_uv > CURRENT_SCHEMA_VERSION:
                    raise BackupInvalidError("SCHEMA_TOO_NEW", f"Database user_version ({db_uv}) is newer than supported.")
            except sqlite3.DatabaseError as exc:
                raise BackupInvalidError("DB_CORRUPT", f"Corrupt SQLite database: {exc}") from exc
            finally:
                if conn is not None:
                    conn.close()
                if tmp_db_path.exists():
                    tmp_db_path.unlink(missing_ok=True)

            return manifest

    except BackupInvalidError:
        raise
    except zipfile.BadZipFile as exc:
        raise BackupInvalidError("DB_CORRUPT", f"Bad zip archive: {exc}") from exc
    except Exception as exc:
        raise BackupInvalidError("DB_CORRUPT", f"Verification failure: {exc}") from exc


def restore_backup(
    zip_path: Path | str,
    data_dir: Path | str | None = None,
    force: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    """Pre-validate backup archive, generate safety backup of current data, and atomically swap files.

    Returns:
        Path to the safety backup created before restoration.
    """
    resolved_data = resolve_data_dir(data_dir)
    zip_path_obj = Path(zip_path).resolve()

    # Pre-validate before touching anything. Raises BackupInvalidError on failure.
    manifest = verify_backup(zip_path_obj)

    target_db = get_db_path(resolved_data)
    target_settings = get_settings_path(resolved_data)

    # 1. Create a safe temporary extraction dir inside data_dir
    temp_extract_dir = Path(tempfile.mkdtemp(prefix=".mmis_restore_tmp_", dir=resolved_data))

    try:
        # Extract members manually by reading bytes and writing to temp dir
        with zipfile.ZipFile(zip_path_obj, "r") as zf:
            ext_db = temp_extract_dir / "mmis.db"
            ext_db.write_bytes(zf.read("mmis.db"))

            ext_settings = None
            if "settings.json" in zf.namelist():
                ext_settings = temp_extract_dir / "settings.json"
                ext_settings.write_bytes(zf.read("settings.json"))

        # Re-verify extracted files
        if _sha256_file(ext_db).lower() != manifest["files"]["mmis.db"].lower():
            raise BackupInvalidError("HASH_MISMATCH", "SHA256 mismatch on extracted DB.")

        # 2. Make automatic safety backup of current data BEFORE swapping
        now_fn = clock or (lambda: datetime.now(timezone.utc))
        ts_str = now_fn().strftime("%Y%m%d_%H%M%S")
        safety_path = resolved_data / f"mmis_pre_restore_{ts_str}.zip"

        # If safety backup already exists, append counter
        counter = 1
        while safety_path.exists():
            safety_path = resolved_data / f"mmis_pre_restore_{ts_str}_{counter}.zip"
            counter += 1

        create_backup(
            data_dir=resolved_data,
            out_path=safety_path,
            force=True,
            clock=now_fn,
        )

        # 3. Atomically swap files into place
        ext_db.replace(target_db)

        # Safely remove old WAL/SHM files to avoid stale transactions
        wal_file = target_db.with_name(target_db.name + "-wal")
        shm_file = target_db.with_name(target_db.name + "-shm")
        if wal_file.exists():
            wal_file.unlink(missing_ok=True)
        if shm_file.exists():
            shm_file.unlink(missing_ok=True)

        if ext_settings and ext_settings.is_file():
            ext_settings.replace(target_settings)

        return safety_path

    finally:
        # Guarantee cleanup of temporary extraction directory
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
