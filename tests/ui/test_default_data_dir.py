"""Tests for default data directory resolution in the GUI shell and startup."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_log_handlers():
    logger = logging.getLogger("mmis.ui")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    yield
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_main_window_default_data_dir(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    env_data = (tmp_path / "env_data").resolve()
    monkeypatch.setenv("MMIS_DATA_DIR", str(env_data))

    service = create_profile_service(None)
    window = MainWindow(service=service, data_dir=None)
    window.show()

    assert window.data_dir == env_data
    assert window.backup_page.data_dir == env_data
    window.close()


def test_main_window_appdata_fallback(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    roaming = (tmp_path / "roaming").resolve()
    monkeypatch.delenv("MMIS_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(roaming))
    if os.name != "nt":
        monkeypatch.setattr(os, "name", "nt")

    expected_dir = roaming / "MMIS"

    service = create_profile_service(None)
    window = MainWindow(service=service, data_dir=None)
    window.show()

    assert window.data_dir == expected_dir
    assert window.backup_page.data_dir == expected_dir
    assert (expected_dir / "mmis.db").is_file()

    window.close()


def test_launch_gui_default_data_dir(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mmis.ui.app import launch_gui

    env_gui = (tmp_path / "env_gui").resolve()
    monkeypatch.setenv("MMIS_DATA_DIR", str(env_gui))

    code = launch_gui(data_dir=None, auto_close_ms=200)
    assert code == 0
    assert (env_gui / "mmis.db").is_file()


def test_launch_gui_explicit_data_dir(qapp, tmp_path: Path) -> None:
    from mmis.ui.app import launch_gui

    explicit_dir = (tmp_path / "explicit").resolve()
    code = launch_gui(data_dir=explicit_dir, auto_close_ms=200)
    assert code == 0
    assert (explicit_dir / "mmis.db").is_file()
