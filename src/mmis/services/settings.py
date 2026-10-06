"""Application settings persistence using a JSON file in the data directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mmis.services.paths import resolve_data_dir

SETTINGS_FILENAME = "app_settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "window_width": 1440,
    "window_height": 900,
    "window_x": None,
    "window_y": None,
    "sidebar_collapsed": False,
    "last_profile": None,
    "animations_enabled": True,
    "active_page": "dashboard",
}


def get_settings_path(data_dir: str | Path | None = None) -> Path:
    """Return the absolute path to the app_settings.json file."""
    base_dir = resolve_data_dir(data_dir)
    return base_dir / SETTINGS_FILENAME


def load_settings(data_dir: str | Path | None = None) -> dict[str, Any]:
    """Load settings from disk, filling in missing keys with defaults."""
    path = get_settings_path(data_dir)
    settings = dict(DEFAULT_SETTINGS)

    if not path.is_file():
        return settings

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            settings.update(data)
    except Exception:
        # If the file is corrupt or unreadable, fall back to defaults safely
        pass

    return settings


def save_settings(settings: dict[str, Any], data_dir: str | Path | None = None) -> None:
    """Write settings to disk deterministically."""
    path = get_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")

    try:
        temp_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
