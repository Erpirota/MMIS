"""Application shell, navigation, responsive collapse, and settings roundtrip tests (A3)."""

from __future__ import annotations

from pathlib import Path

from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.services.settings import load_settings, save_settings
from mmis.storage.repository import ProfileRepository


def test_settings_persistence(tmp_path: Path):
    data_dir = tmp_path / "settings_test"
    data_dir.mkdir()

    # Default settings load
    initial = load_settings(data_dir)
    assert initial["window_width"] == 1440
    assert initial["sidebar_collapsed"] is False

    # Save and reload modified settings
    initial["window_width"] = 1200
    initial["last_profile"] = "My Custom Pack"
    initial["sidebar_collapsed"] = True
    save_settings(initial, data_dir)

    reloaded = load_settings(data_dir)
    assert reloaded["window_width"] == 1200
    assert reloaded["last_profile"] == "My Custom Pack"
    assert reloaded["sidebar_collapsed"] is True


def test_shell_navigation_and_shortcuts(qapp, tmp_path: Path):
    from mmis.ui.shell import MainWindow

    data_dir = tmp_path / "shell_data"
    data_dir.mkdir()
    repo = ProfileRepository(get_db_path(data_dir))
    service = ProfileService(repo)

    window = MainWindow(service=service, data_dir=data_dir)
    window.show()

    # Initial page is dashboard
    assert window.stack.currentIndex() == 0

    # Switch to profiles via method and check index
    window.switch_page("profiles")
    assert window.stack.currentIndex() == 1

    # Switch to mods
    window.switch_page("mods")
    assert window.stack.currentIndex() == 2

    # Switch to issues
    window.switch_page("issues")
    assert window.stack.currentIndex() == 3

    # Switch to compare
    window.switch_page("compare")
    assert window.stack.currentIndex() == 4

    # Switch back to dashboard
    window.switch_page("dashboard")
    assert window.stack.currentIndex() == 0

    window.close()


def test_sidebar_responsive_collapse(qapp, tmp_path: Path):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QResizeEvent
    from mmis.ui.shell import MainWindow

    data_dir = tmp_path / "shell_responsive"
    data_dir.mkdir()
    repo = ProfileRepository(get_db_path(data_dir))
    service = ProfileService(repo)

    window = MainWindow(service=service, data_dir=data_dir)
    window.show()

    # Expand at 1440 width
    window.resize(1440, 900)
    window.resizeEvent(QResizeEvent(QSize(1440, 900), QSize(960, 600)))
    assert window.sidebar._collapsed is False
    assert window.sidebar.width() == 220

    # Collapse at 960 width
    window.resize(960, 600)
    window.resizeEvent(QResizeEvent(QSize(960, 600), QSize(1440, 900)))
    assert window.sidebar._collapsed is True
    assert window.sidebar.width() == 68

    window.close()
