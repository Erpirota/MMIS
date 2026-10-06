"""Automated screenshot capture tool for MMIS UI states and quality assurance."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

# Ensure offscreen platform has system fonts on Windows
if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

# Disable animations globally for deterministic rendering
os.environ["MMIS_NO_ANIM"] = "1"

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from mmis.services.profiles import ProfileService, create_profile_service
from mmis.ui import strings
from mmis.ui.app import create_app
from mmis.ui.shell import MainWindow
from mmis.ui.widgets.confirm_dialog import ConfirmDialog


def wait_for_record_load(window: MainWindow, app: QApplication, timeout_ms: int = 5000) -> None:
    """Pump event loop until the active record is completely loaded."""
    dash = window.dashboard_page
    start = time.monotonic()
    while dash._body_stack.currentWidget() is dash._state_card and (time.monotonic() - start) * 1000 < timeout_ms:
        app.processEvents()
        time.sleep(0.02)
    if dash._body_stack.currentWidget() is dash._state_card:
        print(f"Error: Timed out after {timeout_ms}ms waiting for profile record load", file=sys.stderr)
        sys.exit(1)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)


def is_image_valid(image_path: Path, min_w: int = 300, min_h: int = 150) -> bool:
    """Verify that saved image exists, meets dimensions, and is not blank."""
    if not image_path.is_file() or image_path.stat().st_size == 0:
        return False

    qimg = QImage(str(image_path))
    if qimg.width() < min_w or qimg.height() < min_h:
        return False

    # Sample pixels to ensure it's not a solid blank color (sample fine grid)
    colors = set()
    step_x = max(1, qimg.width() // 80)
    step_y = max(1, qimg.height() // 80)
    for x in range(0, qimg.width(), step_x):
        for y in range(0, qimg.height(), step_y):
            colors.add(qimg.pixel(x, y))
            if len(colors) > 8:
                return True

    return len(colors) > 8


def capture_all_states(
    data_dir: Path,
    out_dir: Path,
    width: int = 1440,
    height: int = 900,
) -> int:
    """Walk every UI page and state, capturing screenshots."""
    out_dir.mkdir(parents=True, exist_ok=True)
    app = create_app()

    service = create_profile_service(data_dir)
    window = MainWindow(service=service, data_dir=data_dir)
    window.resize(width, height)
    window.show()
    app.processEvents()

    captured_files: list[Path] = []

    def grab_to(filename: str, widget=window) -> None:
        if filename != "toast.png":
            window.toast_overlay.hide()
        else:
            window.toast_overlay.show()
        app.processEvents()
        target = out_dir / filename
        pixmap = widget.grab()
        pixmap.save(str(target), "PNG")
        captured_files.append(target)
        print(f"Captured: {filename} ({pixmap.width()}x{pixmap.height()})")

    # 1. dashboard-empty (no profiles)
    window.resize(width, height)
    # Let the record load of the restored profile finish, or it would replace the forced state
    window.task_manager.wait_for_all(5000)
    for _ in range(25):
        app.processEvents()
        time.sleep(0.02)
    window.switch_page("dashboard")
    window.dashboard_page.show_empty_no_profiles()
    grab_to("dashboard-empty.png")

    # 2. profiles-empty
    window.switch_page("profiles")
    window.profiles_page._scroll_area.setVisible(False)
    window.profiles_page._state_card.show_empty(
        title=strings.STATE_EMPTY_TITLE,
        description=strings.PROFILES_EMPTY_DESC,
        action_text=strings.PROFILES_ADD_BTN,
    )
    grab_to("profiles-empty.png")

    # 3. profiles-populated
    window.switch_page("profiles")
    window.profiles_page.refresh()
    grab_to("profiles-populated.png")

    # 4. dashboard-populated
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("dashboard")
    grab_to("dashboard-populated.png")

    # 5. dashboard-never-scanned
    window.set_active_profile("Never Scanned Pack")
    window.switch_page("dashboard")
    grab_to("dashboard-never-scanned.png")

    # 6. mods-table
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("mods")
    window.mods_page.close_drawer()
    grab_to("mods-table.png")

    # 7. mods-filtered (search text + a filter active)
    window.switch_page("mods")
    window.mods_page._search_input.setText("mod")
    window.mods_page._status_combo.setCurrentText(strings.MODS_STATUS_OK)
    grab_to("mods-filtered.png")

    # 8. mods-detail-drawer (split, >=1200 wide)
    window.resize(1440, 900)
    app.processEvents()
    window.switch_page("mods")
    window.mods_page.reset_filters()
    # Select first row to open drawer
    if window.mods_page._model.rowCount() > 0:
        row = window.mods_page._model.get_row(0)
        if row:
            window.mods_page.open_drawer(row)
    app.processEvents()
    grab_to("mods-detail-drawer.png")

    # 9. mods-detail-overlay (960 wide)
    window.resize(960, 600)
    app.processEvents()
    window.switch_page("mods")
    window.mods_page.update_layout_mode()
    app.processEvents()
    grab_to("mods-detail-overlay.png")

    # 10. mods-empty-result (filter matches nothing)
    window.resize(width, height)
    window.switch_page("mods")
    window.mods_page.close_drawer()
    window.mods_page._search_input.setText("zzzz_unmatched_query_xyz")
    grab_to("mods-empty-result.png")

    # 11. loading state (scan in progress)
    window.switch_page("dashboard")
    window.dashboard_page.show_loading()
    grab_to("loading-state.png")

    # 12. error state (profile path missing)
    window.set_active_profile("Missing Path Pack")
    window.switch_page("dashboard")
    grab_to("error-state.png")

    # 13. sidebar-collapsed (960x600)
    window.resize(960, 600)
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("dashboard")
    window.sidebar.set_collapsed(True)
    grab_to("sidebar-collapsed.png")

    # 14. confirmation dialog
    window.resize(width, height)
    dlg = ConfirmDialog(
        title=strings.DIALOG_DELETE_CONFIRM_TITLE,
        message=strings.DIALOG_DELETE_CONFIRM_PROMPT.format(name="Demo Modpack"),
        warning_note=strings.DIALOG_DELETE_CONFIRM_WARNING,
        parent=window,
    )
    dlg.show()
    grab_to("confirmation-dialog.png", widget=dlg)
    dlg.close()

    # 15. toast notification
    window.switch_page("dashboard")
    window.toast_overlay.show()
    window.toast_overlay.show_toast(
        title=strings.APP_SHORT_TITLE,
        message=strings.TOAST_SCAN_SUCCESS.format(name="Demo Modpack"),
        kind="success",
        duration_ms=0,
    )
    window.toast_overlay.show()
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)
    grab_to("toast.png")
    window.toast_overlay.hide()
    app.processEvents()

    # 16. issues-all
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("issues")
    window.issues_page.reset_filters()
    grab_to("issues-all.png")

    # 17. issues-filtered-possible
    window.issues_page._chip_certain.setChecked(False)
    window.issues_page._chip_insufficient.setChecked(False)
    window.issues_page._chip_possible.setChecked(True)
    grab_to("issues-filtered-possible.png")

    # 18. issues-item-expanded
    window.issues_page.reset_filters()
    app.processEvents()
    if window.issues_page._issue_cards:
        window.issues_page._issue_cards[0].set_expanded(True)
        app.processEvents()
    grab_to("issues-item-expanded.png")
    if window.issues_page._issue_cards:
        window.issues_page._issue_cards[0].set_expanded(False)
        app.processEvents()

    # 19. issues-empty (Clean modpack with 0 issues)
    window.set_active_profile("Clean Modpack")
    wait_for_record_load(window, app)
    window.switch_page("issues")
    grab_to("issues-empty.png")

    # 20. compare-default
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("compare")
    grab_to("compare-default.png")

    # 21. compare-same-scan-hint
    window.compare_page._combo_a.setCurrentIndex(0)
    window.compare_page._combo_b.setCurrentIndex(0)
    grab_to("compare-same-scan-hint.png")

    # 22. compare-empty (fewer than two scans)
    window.set_active_profile("Never Scanned Pack")
    window.switch_page("compare")
    grab_to("compare-empty.png")

    # 23. crashes-list
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("crashes")
    window.crashes_page.set_scope("all")
    window.crashes_page.refresh()
    app.processEvents()
    grab_to("crashes-list.png")

    # 24. crashes-detail
    v_ids = window.crashes_page.visible_crash_ids()
    if v_ids:
        window.crashes_page.select_crash(v_ids[0])
        app.processEvents()
    grab_to("crashes-detail.png")

    # 25. crashes-empty
    window.set_active_profile("Never Scanned Pack")
    window.switch_page("crashes")
    window.crashes_page.set_scope("active")
    window.crashes_page.refresh()
    app.processEvents()
    grab_to("crashes-empty.png")

    # 26. reports-default
    window.set_active_profile("Demo Modpack")
    wait_for_record_load(window, app)
    window.switch_page("reports")
    app.processEvents()
    start = time.monotonic()
    while window.reports_page.current_report() is None and (time.monotonic() - start) < 5:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)
    grab_to("reports-default.png")

    # 27. reports-empty
    window.set_active_profile("Never Scanned Pack")
    window.switch_page("reports")
    app.processEvents()
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)
    grab_to("reports-empty.png")

    # 28. backup-empty
    b_dir = data_dir / "backups"
    stashed_backups = []
    if b_dir.is_dir():
        for f in list(b_dir.iterdir()):
            tmp_stash = f.with_suffix(f.suffix + ".stash")
            f.rename(tmp_stash)
            stashed_backups.append((tmp_stash, f))
    window.switch_page("backup")
    window.backup_page.refresh()
    app.processEvents()
    grab_to("backup-empty.png")

    # 29. backup-populated
    for tmp_stash, orig in stashed_backups:
        tmp_stash.rename(orig)
    if not list(b_dir.glob("*.zip")):
        from mmis.services.backup import create_backup as svc_create_backup
        from mmis.services.backup_catalog import new_backup_path
        svc_create_backup(data_dir, new_backup_path(data_dir))
    window.backup_page.refresh()
    app.processEvents()
    grab_to("backup-populated.png")

    window.close()

    # Validate all captures
    print("\nValidating captured screenshots...")
    failed = False
    for img_path in captured_files:
        valid = is_image_valid(img_path)
        print(f"  [{'PASS' if valid else 'FAIL'}] {img_path.name}")
        if not valid:
            failed = True

    if failed:
        print("Error: One or more screenshots were invalid or blank!", file=sys.stderr)
        return 1

    print(f"Successfully generated all {len(captured_files)} screenshots.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture MMIS UI screenshot gallery.")
    parser.add_argument("--out", required=True, help="Output directory for PNG screenshots")
    parser.add_argument("--data-dir", required=True, help="MMIS application data directory")
    parser.add_argument("--size", default="1440x900", help="Window dimensions (WxH, default: 1440x900)")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    data_dir = Path(args.data_dir).resolve()

    try:
        w_str, h_str = args.size.lower().split("x")
        w, h = int(w_str), int(h_str)
    except Exception:
        w, h = 1440, 900

    return capture_all_states(data_dir=data_dir, out_dir=out_dir, width=w, height=h)


if __name__ == "__main__":
    sys.exit(main())
