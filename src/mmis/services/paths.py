"""Resolution of application data directory and database paths."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_data_dir(custom_dir: Path | str | None = None) -> Path:
    """Resolve the MMIS data directory path.

    Order of precedence:
    1. Explicit custom_dir argument (e.g. from CLI --data-dir)
    2. MMIS_DATA_DIR environment variable
    3. Platform default (%APPDATA%\\MMIS on Windows, ~/.local/share/mmis on Unix)
    """
    if custom_dir is not None:
        target = Path(custom_dir).resolve()
    elif os.environ.get("MMIS_DATA_DIR"):
        target = Path(os.environ["MMIS_DATA_DIR"]).resolve()
    elif os.name == "nt" and os.environ.get("APPDATA"):
        target = Path(os.environ["APPDATA"]) / "MMIS"
    else:
        target = Path.home() / ".local" / "share" / "mmis"

    target.mkdir(parents=True, exist_ok=True)
    return target


def get_db_path(custom_dir: Path | str | None = None) -> Path:
    """Return the absolute path to mmis.db, ensuring the data directory exists."""
    data_dir = resolve_data_dir(custom_dir)
    return data_dir / "mmis.db"
