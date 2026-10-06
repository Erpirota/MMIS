"""Backup catalog and discovery service.

Thin service module for enumerating managed and safety backup archives,
inspecting manifest metadata without unpacking or verifying archives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Callable
import zipfile


@dataclass(frozen=True)
class BackupEntry:
    """Summary record of a discovered backup archive."""

    path: Path
    file_name: str
    size_bytes: int
    modified_at: str
    kind: str  # "manual" | "pre_restore"
    manifest_ok: bool
    created_at: str | None
    app_version: str | None
    schema_version: int | None
    error: str | None


def backups_dir(data_dir: Path | str) -> Path:
    """Return the managed backups folder path inside the data directory."""
    return Path(data_dir).resolve() / "backups"


def new_backup_path(data_dir: Path | str, clock: Callable[[], datetime] | None = None) -> Path:
    """Generate a collision-free path for a new manual backup archive in UTC."""
    bdir = backups_dir(data_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    ts = now_fn().strftime("%Y%m%d_%H%M%S")
    base_name = f"mmis_backup_{ts}"
    candidate = bdir / f"{base_name}.zip"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = bdir / f"{base_name}_{counter}.zip"
        if not candidate.exists():
            return candidate
        counter += 1


def list_backups(data_dir: Path | str) -> list[BackupEntry]:
    """List all managed and pre-restore backup archives, ordered newest first.

    Reads only archive directory headers and manifest.json (capped at 64 KB).
    Never unpacks mmis.db or runs full verification. Never raises for bad files.
    """
    resolved_data = Path(data_dir).resolve()
    candidates: list[tuple[Path, str]] = []

    # 1. Managed manual backups
    bdir = backups_dir(resolved_data)
    if bdir.is_dir():
        for p in bdir.glob("*.zip"):
            if p.is_file():
                candidates.append((p, "manual"))

    # 2. Safety pre-restore backups in root data directory
    if resolved_data.is_dir():
        for p in resolved_data.glob("mmis_pre_restore_*.zip"):
            if p.is_file():
                candidates.append((p, "pre_restore"))

    # Sort by mtime descending (newest first)
    candidates.sort(key=lambda item: item[0].stat().st_mtime if item[0].exists() else 0, reverse=True)

    entries: list[BackupEntry] = []
    for path, kind in candidates:
        try:
            st = path.stat()
            size_bytes = st.st_size
            mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            modified_at = mtime_dt.isoformat()
        except Exception as exc:
            size_bytes = 0
            modified_at = ""

        manifest_ok = False
        created_at: str | None = None
        app_version: str | None = None
        schema_version: int | None = None
        error: str | None = None

        try:
            with zipfile.ZipFile(path, "r") as zf:
                namelist = zf.namelist()
                if "manifest.json" not in namelist:
                    error = "MISSING_MANIFEST"
                else:
                    zinfo = zf.getinfo("manifest.json")
                    if zinfo.file_size > 65536:
                        error = "MANIFEST_TOO_LARGE"
                    else:
                        data = zf.read(zinfo)
                        man = json.loads(data.decode("utf-8"))
                        if not isinstance(man, dict):
                            error = "INVALID_MANIFEST"
                        else:
                            manifest_ok = True
                            created_at = man.get("created_at")
                            app_version = man.get("app_version")
                            raw_schema = man.get("schema_version")
                            schema_version = int(raw_schema) if raw_schema is not None else None
        except Exception as exc:
            error = str(exc)

        entries.append(
            BackupEntry(
                path=path,
                file_name=path.name,
                size_bytes=size_bytes,
                modified_at=modified_at,
                kind=kind,
                manifest_ok=manifest_ok,
                created_at=created_at,
                app_version=app_version,
                schema_version=schema_version,
                error=error,
            )
        )

    return entries
