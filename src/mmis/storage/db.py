"""SQLite database connection management and initialization."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from mmis.domain.errors import SchemaTooNewError, StorageError
from mmis.storage.migrations import apply_migrations


def connect_db(db_path: Path | str) -> sqlite3.Connection:
    """Open and initialize a connection to the SQLite database.

    Enforces foreign keys, busy timeout, WAL mode, and verifies migrations.
    """
    path_obj = Path(db_path)
    if str(db_path) != ":memory:":
        if path_obj.parent and not path_obj.parent.exists():
            path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn = None
    try:
        conn = sqlite3.connect(
            str(db_path),
            timeout=10.0,
            check_same_thread=False,
            autocommit=True,
        )
        conn.row_factory = sqlite3.Row

        # Basic integrity and settings
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        if str(db_path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")

        # Quick check for invalid / corrupt SQLite file
        conn.execute("PRAGMA schema_version;").fetchone()
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
        raise StorageError(f"Database file '{db_path}' is corrupted or not a valid SQLite database: {exc}") from exc
    except Exception as exc:
        if conn is not None:
            conn.close()
        raise StorageError(f"Failed to open database '{db_path}': {exc}") from exc

    try:
        apply_migrations(conn, str(db_path))
    except SchemaTooNewError:
        conn.close()
        raise
    except Exception:
        conn.close()
        raise

    return conn
