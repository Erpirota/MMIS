"""Master acceptance check for M7-5: the packaged GUI on real data, page by page.

Usage: check_m7_package_gui.py <dist_dir> <seeded_data_dir> <out_dir>
       (project venv python; needs an interactive desktop session)

Starts dist/MMIS/MMIS.exe on a data dir seeded by scripts/seed_demo_data.py,
walks the eight pages with the Ctrl+1..Ctrl+8 shortcuts and saves a capture of
the real window for each page. Keys are sent only while the MMIS window is the
foreground window. The process must stay alive through the walk and close
cleanly on WM_CLOSE.
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtGui import QGuiApplication

dist, data, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
out.mkdir(parents=True, exist_ok=True)
PAGES = ["dashboard", "profiles", "mods", "issues", "compare", "crashes", "reports", "backup"]
failed = []
user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
VK_CONTROL, KEYUP, WM_CLOSE = 0x11, 0x0002, 0x0010


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)
    return bool(cond)


def main_window(pid):
    found = []

    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if rect.right - rect.left > 400:
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found[0] if found else (None, "")


def focus(hwnd):
    user32.ShowWindow(hwnd, 9)
    # A foreground change is only allowed after some input from this process
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, KEYUP, 0)
    time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def ctrl(digit):
    vk = 0x30 + digit
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


env = {
    "SystemRoot": os.environ["SystemRoot"],
    "PATH": rf"{os.environ['SystemRoot']}\System32;{os.environ['SystemRoot']}",
    "TEMP": os.environ["TEMP"], "TMP": os.environ["TEMP"],
    "APPDATA": str(out / "_appdata"), "LOCALAPPDATA": str(out / "_appdata"), "USERPROFILE": str(out / "_home"),
    "MMIS_NO_ANIM": "1",
}
app = QGuiApplication([])
proc = subprocess.Popen([str(dist / "MMIS.exe"), "--data-dir", str(data)], env=env, cwd=str(out), stdin=subprocess.DEVNULL)
try:
    hwnd, title = None, ""
    t0 = time.monotonic()
    while hwnd is None and proc.poll() is None and time.monotonic() - t0 < 60:
        hwnd, title = main_window(proc.pid)
        time.sleep(0.2)
    check("packaged GUI: main window appears", hwnd is not None and "Unhandled" not in title, f"title={title!r} rc={proc.poll()}")
    if hwnd:
        time.sleep(2.5)  # async record load of the restored profile
        screen = QGuiApplication.primaryScreen()
        for i, page in enumerate(PAGES, start=1):
            if not any(focus(hwnd) for _ in range(4)):
                check(f"packaged GUI: {page} - window has focus", False, "could not bring the window to the foreground")
                continue
            ctrl(i)
            time.sleep(1.2)
            shot = screen.grabWindow(int(hwnd))
            path = out / f"{i}-{page}.png"
            shot.save(str(path))
            img = shot.toImage()
            colours = {img.pixel(x, y) for x in range(0, img.width(), 37) for y in range(0, img.height(), 29)}
            check(f"packaged GUI: {page} captured ({img.width()}x{img.height()}, {len(colours)} colours)", img.width() > 400 and len(colours) > 8)
            check(f"packaged GUI: alive after opening {page}", proc.poll() is None, f"rc={proc.poll()}")
            now_hwnd, now_title = main_window(proc.pid)
            check(f"packaged GUI: no error dialog after {page}", "Unhandled" not in now_title, now_title)
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            rc = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            rc = None
        check("packaged GUI: closes cleanly on WM_CLOSE", rc == 0, f"rc={rc}")
finally:
    if proc.poll() is None:
        proc.kill()

logs = list(data.rglob("*.log"))
bad = [p.name for p in logs if any(k in p.read_text(encoding="utf-8", errors="replace") for k in ("Traceback", "ERROR"))]
check("packaged GUI: UI log has no error or traceback", bool(logs) and not bad, f"logs={[p.name for p in logs]} bad={bad}")
print("FAILED:", failed)
sys.exit(1 if failed else 0)
