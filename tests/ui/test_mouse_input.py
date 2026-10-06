"""Mouse input reaches the controls.

Regression tests for the full-window ToastOverlay that sat above every page and
swallowed all mouse clicks. Unlike the other UI tests, these never call
``button.click()``: clicks are sent to the top-level window at a position and
must be routed by Qt's own hit-testing to the control under the cursor.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

SIZES = [(1440, 900), (1200, 750), (960, 600)]
PAGES = ["dashboard", "profiles", "mods", "issues", "compare", "crashes", "reports", "backup"]


@pytest.fixture
def seeded_data(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    script = Path(__file__).resolve().parents[2] / "scripts" / "seed_demo_data.py"
    subprocess.run(
        [sys.executable, str(script), "--data-dir", str(data_dir), "--pack-dir", str(tmp_path / "pack")],
        check=True,
        timeout=60,
    )
    return data_dir


def _pump(qapp, ms: int = 150) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def _open_window(qapp, data_dir: Path, size: tuple[int, int], profile: str | None = None):
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    window = MainWindow(service=create_profile_service(data_dir), data_dir=data_dir)
    window.resize(*size)
    window.show()
    _pump(qapp)
    if profile:
        window.set_active_profile(profile)
        window.task_manager.wait_for_all(5000)
        _pump(qapp, 400)
    return window


def _interactive_widgets(window):
    """Visible, enabled controls a user can aim at, with the point to click."""
    from PySide6.QtWidgets import QAbstractButton, QAbstractItemView, QComboBox, QLineEdit

    found = []
    for cls in (QAbstractButton, QComboBox, QLineEdit, QAbstractItemView):
        for widget in window.findChildren(cls):
            if not widget.isVisible() or not widget.isEnabled():
                continue
            target = widget.viewport() if isinstance(widget, QAbstractItemView) else widget
            point = target.mapTo(window, target.rect().center())
            if window.rect().contains(point) and target.visibleRegion().contains(target.rect().center()):
                found.append((widget, point))
    return found


def _intercepted(window):
    """Controls whose click position is covered by a widget that is not theirs."""
    blocked = []
    for widget, point in _interactive_widgets(window):
        hit = window.childAt(point)
        if hit is not widget and not (hit is not None and widget.isAncestorOf(hit)):
            blocked.append(f"{type(widget).__name__} at {point.x()},{point.y()} covered by {type(hit).__name__}")
    return blocked


def _click_window(window, point) -> None:
    """Send a real left click to the top-level window; Qt decides which widget receives it."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def test_overlay_is_hidden_without_toasts(qapp, tmp_path: Path):
    window = _open_window(qapp, tmp_path / "empty", (1440, 900))
    overlay = window.toast_overlay
    assert not overlay.isVisible()
    assert not overlay.has_toasts()
    window.resize(1200, 750)
    _pump(qapp)
    assert not overlay.isVisible(), "resizing the window must not bring the empty overlay back"
    window.close()


@pytest.mark.parametrize("size", SIZES)
def test_no_control_is_covered_on_any_page_first_run(qapp, tmp_path: Path, size):
    """Fresh installation (no profiles): the state a new user starts in."""
    window = _open_window(qapp, tmp_path / "empty", size)
    for page in PAGES:
        window.switch_page(page)
        _pump(qapp)
        assert _interactive_widgets(window), f"{page}: no interactive widget found"
        assert not _intercepted(window), f"{page} at {size}: {_intercepted(window)[:3]}"
    window.close()


@pytest.mark.parametrize("size", SIZES)
def test_no_control_is_covered_on_any_page_with_data(qapp, seeded_data: Path, size):
    window = _open_window(qapp, seeded_data, size, profile="Demo Modpack")
    for page in PAGES:
        window.switch_page(page)
        _pump(qapp, 250)
        assert not _intercepted(window), f"{page} at {size}: {_intercepted(window)[:3]}"
    window.close()


def test_real_click_on_sidebar_switches_page(qapp, seeded_data: Path):
    from PySide6.QtWidgets import QAbstractButton

    window = _open_window(qapp, seeded_data, (1440, 900), profile="Demo Modpack")
    window.switch_page("dashboard")
    _pump(qapp)
    nav = {b.text().strip(): b for b in window.sidebar.findChildren(QAbstractButton) if b.isVisible()}
    for label, attr in (("Mods", "mods_page"), ("Issues", "issues_page"), ("Backup", "backup_page"), ("Dashboard", "dashboard_page")):
        button = nav[label]
        _click_window(window, button.mapTo(window, button.rect().center()))
        _pump(qapp, 250)
        assert getattr(window, attr).isVisible(), f"clicking '{label}' in the sidebar did not open its page"
    window.close()


def test_real_click_reaches_page_controls(qapp, seeded_data: Path):
    from PySide6.QtWidgets import QCheckBox, QTableView

    window = _open_window(qapp, seeded_data, (1440, 900), profile="Demo Modpack")
    window.switch_page("mods")
    _pump(qapp, 300)
    page = window.mods_page

    table = next(t for t in page.findChildren(QTableView) if t.isVisible())
    row_point = table.viewport().mapTo(window, table.visualRect(table.model().index(2, 1)).center())
    _click_window(window, row_point)
    _pump(qapp)
    assert table.currentIndex().row() == 2, "clicking a table row did not select it"

    checkbox = next(c for c in page.findChildren(QCheckBox) if c.isVisible())
    before = checkbox.isChecked()
    _click_window(window, checkbox.mapTo(window, checkbox.rect().center()))
    _pump(qapp)
    assert checkbox.isChecked() != before, "clicking the 'Issues only' filter did not toggle it"
    window.close()


def test_toast_covers_only_itself_and_can_be_closed_by_click(qapp, seeded_data: Path):
    from PySide6.QtWidgets import QAbstractButton

    window = _open_window(qapp, seeded_data, (1440, 900), profile="Demo Modpack")
    window.switch_page("dashboard")
    overlay = window.toast_overlay
    toast = overlay.show_toast(title="Done", message="Scan completed.", kind="success", duration_ms=0)
    _pump(qapp)

    assert overlay.isVisible()
    assert overlay.geometry().contains(toast.geometry().translated(overlay.pos()))
    assert overlay.width() < window.width() / 2 and overlay.height() < window.height() / 2, (
        f"overlay {overlay.geometry().getRect()} covers far more than the toast"
    )
    assert overlay.geometry().right() <= window.rect().right() and overlay.geometry().bottom() <= window.rect().bottom()

    # Controls away from the toast still receive clicks while it is shown
    nav = {b.text().strip(): b for b in window.sidebar.findChildren(QAbstractButton) if b.isVisible()}
    _click_window(window, nav["Mods"].mapTo(window, nav["Mods"].rect().center()))
    _pump(qapp, 250)
    assert window.mods_page.isVisible()

    # The toast's own close button is reachable by a real click
    close_btn = next(b for b in toast.findChildren(QAbstractButton) if b.isVisible())
    _click_window(window, close_btn.mapTo(window, close_btn.rect().center()))
    _pump(qapp)
    assert not overlay.has_toasts()
    assert not overlay.isVisible(), "the overlay must disappear with its last toast"
    assert not _intercepted(window)
    window.close()


def test_auto_dismissed_toast_releases_the_window(qapp, tmp_path: Path):
    window = _open_window(qapp, tmp_path / "empty", (960, 600))
    overlay = window.toast_overlay
    overlay.show_toast(title="Saved", message="Profile added.", kind="info", duration_ms=120)
    _pump(qapp, 60)
    assert overlay.isVisible()
    _pump(qapp, 400)
    assert not overlay.isVisible()
    assert not _intercepted(window)
    window.close()
