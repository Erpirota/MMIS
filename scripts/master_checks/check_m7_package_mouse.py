"""Master acceptance check: REAL mouse clicks on the packaged GUI.

Usage: check_m7_package_mouse.py <path-to-MMIS.exe> <out_dir> [<seeded_data_dir>]
       (project venv python, PYTHONPATH=src, interactive desktop session)

Without a data dir the exe is started exactly as a user does (no arguments,
its own %APPDATA%\\MMIS). With a seeded data dir (scripts/seed_demo_data.py) it
is started with --data-dir and the data-heavy pages are exercised.

The cursor is moved with SetCursorPos and the left button is pressed with
mouse_event, so the clicks travel through Windows and Qt hit-testing like a
user's. A click is sent only when the window under the cursor belongs to the
MMIS process. Where to click is read from an in-process copy of the same
window (same size, a copy of the same data); whether the click worked is
judged from captures of the real window before and after.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

os.environ["MMIS_NO_ANIM"] = "1"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QCheckBox, QTableView

from mmis.services.profiles import create_profile_service
from mmis.ui.app import create_app
from mmis.ui.shell import MainWindow

exe, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
seeded = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None
out.mkdir(parents=True, exist_ok=True)
failed = []
user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
LEFTDOWN, LEFTUP, KEYUP, WM_CLOSE = 0x0002, 0x0004, 0x0002, 0x0010


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)
    return bool(cond)


def windows_of(pid):
    found = []

    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left > 60 and rect.bottom - rect.top > 30:
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def client_origin(hwnd):
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return pt.x, pt.y, rect.right, rect.bottom


def focus(hwnd):
    for _ in range(4):
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, KEYUP, 0)
        time.sleep(0.25)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def real_click(pid, hwnd, x, y):
    """Left click at client coordinates of hwnd. Returns False if another program is under the cursor."""
    ox, oy, _, _ = client_origin(hwnd)
    sx, sy = ox + int(x), oy + int(y)
    focus(hwnd)
    user32.SetCursorPos(sx, sy)
    time.sleep(0.15)
    under = user32.WindowFromPoint(wintypes.POINT(sx, sy))
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(under, ctypes.byref(owner))
    if owner.value != pid:
        return False
    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(LEFTUP, 0, 0, 0, 0)
    time.sleep(0.7)
    return True


app = create_app()
screen = app.primaryScreen()
shot_no = [0]


def capture(hwnd, label):
    shot_no[0] += 1
    img = screen.grabWindow(int(hwnd)).toImage()
    img.save(str(out / f"{shot_no[0]:02d}-{label}.png"))
    return img


def changed(a, b, rect=None):
    """Share of sampled pixels that differ between two captures (optionally inside rect x, y, w, h)."""
    if a.size() != b.size():
        return 1.0
    x0, y0, w, h = rect or (0, 0, a.width(), a.height())
    step = 2 if rect else 4
    total = diff = 0
    for y in range(max(0, y0), min(a.height(), y0 + h), step):
        for x in range(max(0, x0), min(a.width(), x0 + w), step):
            total += 1
            diff += a.pixel(x, y) != b.pixel(x, y)
    return diff / max(1, total)


def pump(ms=250):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def center(model, widget):
    target = widget.viewport() if isinstance(widget, QTableView) else widget
    p = target.mapTo(model, target.rect().center())
    return p.x(), p.y()


# --- start the real program ----------------------------------------------------
args = [str(exe)] + (["--data-dir", str(seeded)] if seeded else [])
env = dict(os.environ)
env.pop("MMIS_DATA_DIR", None)
env.pop("MMIS_AUTO_CLOSE_MS", None)
env.pop("QT_QPA_PLATFORM", None)
proc = subprocess.Popen(args, cwd=str(exe.parent), env=env, stdin=subprocess.DEVNULL)
try:
    t0 = time.monotonic()
    main = None
    while main is None and proc.poll() is None and time.monotonic() - t0 < 60:
        ws = windows_of(proc.pid)
        main = ws[0] if ws else None
        time.sleep(0.2)
    if not check("window appears", main is not None, f"rc={proc.poll()}"):
        raise SystemExit
    time.sleep(2.5)
    _, _, cw, ch = client_origin(main)
    print(f"     real window client area {cw}x{ch}, started as: {' '.join(args)}")

    # in-process model of the same window, on a copy of the same data
    model_dir = out / "_model_data"
    shutil.rmtree(model_dir, ignore_errors=True)
    source = seeded if seeded else Path(os.environ["APPDATA"]) / "MMIS"
    model_dir.mkdir(parents=True)
    for name in ("mmis.db", "app_settings.json"):
        if (source / name).exists():
            shutil.copy2(source / name, model_dir / name)
    model = MainWindow(service=create_profile_service(model_dir), data_dir=model_dir)
    # The model is only measured, never looked at: keep it off the screen so it can
    # neither cover the real window nor receive the clicks meant for it.
    model.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    model.resize(cw, ch)
    model.show()
    pump(600)
    model.task_manager.wait_for_all(5000)
    pump(400)
    nav = {b.text().strip(): b for b in model.sidebar.findChildren(QAbstractButton) if b.isVisible()}

    def click_nav(label, page_attr):
        before = capture(main, f"before-{label.lower().replace(' ', '-')}")
        sent = real_click(proc.pid, main, *center(model, nav[label]))
        after = capture(main, f"after-{label.lower().replace(' ', '-')}")
        ratio = changed(before, after)
        # Two empty pages can look almost alike, but the highlighted sidebar entry always moves
        side = model.sidebar
        sp = side.mapTo(model, side.rect().topLeft())
        side_ratio = changed(before, after, (sp.x(), sp.y(), side.width(), side.height()))
        check(f"sidebar '{label}': click delivered to MMIS", sent, "another window was under the cursor")
        check(f"sidebar '{label}': the page changed ({ratio:.1%} of the window, {side_ratio:.1%} of the sidebar repainted)",
              side_ratio > 0.01 and ratio > 0.002, f"window {ratio:.2%}, sidebar {side_ratio:.2%}")
        model.switch_page(page_attr)
        pump(300)

    for label, page in (("Profiles", "profiles"), ("Mods", "mods"), ("Issues", "issues"), ("Compare", "compare"),
                        ("Crash Logs", "crashes"), ("Reports", "reports"), ("Backup", "backup"), ("Dashboard", "dashboard")):
        click_nav(label, page)

    # a button that opens a dialog, and a button inside that dialog
    click_nav("Profiles", "profiles")
    add_btn = model.profiles_page._add_btn
    n_before = len(windows_of(proc.pid))
    sent = real_click(proc.pid, main, *center(model, add_btn))
    time.sleep(0.6)
    ws = windows_of(proc.pid)
    dialogs = [w for w in ws if w != main]
    check("'Add Profile' button: click opens the dialog", sent and len(ws) == n_before + 1, f"windows before={n_before} after={len(ws)}")
    if dialogs:
        dlg = dialogs[0]
        capture(dlg, "add-profile-dialog")
        from mmis.ui.pages.profiles import AddProfileDialog

        mdlg = AddProfileDialog(model.service, model)
        mdlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        mdlg.show()
        pump(300)
        _, _, dw, dh = client_origin(dlg)
        mdlg.resize(dw, dh)
        pump(200)
        cancel = next(b for b in mdlg.findChildren(QAbstractButton) if b.text().strip().lower() == "cancel")
        cp = cancel.mapTo(mdlg, cancel.rect().center())
        mdlg.close()
        pump(100)
        sent = real_click(proc.pid, dlg, cp.x(), cp.y())
        time.sleep(0.5)
        check("dialog 'Cancel' button: click closes the dialog", sent and len(windows_of(proc.pid)) == n_before, f"windows now={len(windows_of(proc.pid))}")

    if seeded:
        # table row, filter checkbox, combo box popup on the Mods page
        click_nav("Mods", "mods")
        mods = model.mods_page
        table = next(t for t in mods.findChildren(QTableView) if t.isVisible())
        rp = table.viewport().mapTo(model, table.visualRect(table.model().index(3, 1)).center())
        before = capture(main, "mods-before-row")
        sent = real_click(proc.pid, main, rp.x(), rp.y())
        after = capture(main, "mods-after-row")
        ratio = changed(before, after)
        check(f"Mods table: clicking a row opens its details ({ratio:.1%} repainted)", sent and ratio > 0.02, f"{ratio:.2%}")
        # close the details again by clicking the same row's neighbour is not needed: reload the page state
        real_click(proc.pid, main, *center(model, nav["Dashboard"]))
        real_click(proc.pid, main, *center(model, nav["Mods"]))
        time.sleep(0.5)

        box = next(c for c in mods.findChildren(QCheckBox) if c.isVisible())
        before = capture(main, "mods-before-filter")
        sent = real_click(proc.pid, main, *center(model, box))
        after = capture(main, "mods-after-filter")
        tp = table.mapTo(model, table.rect().topLeft())
        ratio = changed(before, after, (tp.x(), tp.y(), table.width(), table.height()))
        check(f"Mods 'Issues only' filter: clicking it filters the table ({ratio:.1%} of the table repainted)", sent and ratio > 0.02, f"{ratio:.2%}")
        sent = real_click(proc.pid, main, *center(model, box))

        combo = mods._status_combo if hasattr(mods, "_status_combo") else None
        if combo is not None:
            n_before = len(windows_of(proc.pid))
            sent = real_click(proc.pid, main, *center(model, combo))
            time.sleep(0.4)
            opened = len(windows_of(proc.pid)) > n_before
            popup = [w for w in windows_of(proc.pid) if w != main]
            if popup:
                capture(popup[0], "mods-status-popup")
            check("Mods status filter: clicking the combo box opens its list", sent and opened)
            user32.keybd_event(0x1B, 0, 0, 0)
            user32.keybd_event(0x1B, 0, KEYUP, 0)
            time.sleep(0.4)

        # Issues: expand a card by clicking its header
        click_nav("Issues", "issues")
        cards = model.issues_page._issue_cards
        if cards:
            hp = cards[0]._header_widget.mapTo(model, cards[0]._header_widget.rect().center())
            before = capture(main, "issues-before-expand")
            sent = real_click(proc.pid, main, hp.x() - 100, hp.y())
            after = capture(main, "issues-after-expand")
            ratio = changed(before, after)
            check(f"Issues: clicking a finding expands it ({ratio:.1%} repainted)", sent and ratio > 0.02, f"{ratio:.2%}")

    model.close()
    pump(100)
    user32.PostMessageW(main, WM_CLOSE, 0, 0)
    try:
        rc = proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        rc = None
    check("program closes cleanly", rc == 0, f"rc={rc}")
except SystemExit:
    pass
finally:
    if proc.poll() is None:
        proc.kill()

print("FAILED:", failed)
sys.exit(1 if failed else 0)
