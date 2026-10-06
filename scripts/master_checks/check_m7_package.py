"""Master acceptance check for M7-4 (PyInstaller package).

Usage: check_m7_package.py <dist_dir> <work_dir>   (any python >= 3.14 with the repo as cwd)

<dist_dir> is dist/MMIS as produced by scripts/build_package.py. The frozen
executables are run with a stripped environment (no PYTHONPATH, PATH limited
to Windows, APPDATA redirected into <work_dir>) and from a cwd outside the
repository, and compared with the development CLI on the same inputs.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
dist, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
work.mkdir(parents=True, exist_ok=True)
GUI, CLI = dist / "MMIS.exe", dist / "mmis-cli.exe"
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
failed = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)
    return bool(cond)


fake_appdata = work / "appdata"
fake_appdata.mkdir(exist_ok=True)
CLEAN_ENV = {
    "SystemRoot": os.environ["SystemRoot"],
    "PATH": rf"{os.environ['SystemRoot']}\System32;{os.environ['SystemRoot']}",
    "TEMP": os.environ["TEMP"],
    "TMP": os.environ["TEMP"],
    "APPDATA": str(fake_appdata),
    "LOCALAPPDATA": str(fake_appdata),
    "USERPROFILE": str(work / "home"),
}
(work / "home").mkdir(exist_ok=True)
DEV_ENV = dict(os.environ, PYTHONPATH=str(REPO / "src"), APPDATA=str(fake_appdata), PYTHONIOENCODING="utf-8")
DEV_ENV.pop("MMIS_DATA_DIR", None)


def frozen(*args, timeout=120):
    return subprocess.run([str(CLI), *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=CLEAN_ENV, cwd=str(work), timeout=timeout, stdin=subprocess.DEVNULL)


def dev(*args, timeout=120):
    return subprocess.run([str(VENV_PY), "-m", "mmis", *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=DEV_ENV, cwd=str(work), timeout=timeout, stdin=subprocess.DEVNULL)


real_appdata = Path(os.environ["APPDATA"]) / "MMIS"


def appdata_fingerprint():
    if not real_appdata.exists():
        return None
    return sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in real_appdata.rglob("*"))


before_real = appdata_fingerprint()

# --- 1. Bundle layout -------------------------------------------------------
check("MMIS.exe present", GUI.is_file())
check("mmis-cli.exe present", CLI.is_file())
if not (GUI.is_file() and CLI.is_file()):
    print("FAILED:", failed)
    sys.exit(1)
src_svgs = sorted(p.name for p in (REPO / "src/mmis/ui/assets").glob("*.svg"))
bundled = sorted(p.name for p in dist.rglob("*.svg") if p.parent.as_posix().endswith("mmis/ui/assets"))
check("all SVG assets bundled under mmis/ui/assets", bundled == src_svgs, f"bundled {len(bundled)} of {len(src_svgs)}")
size_mb = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file()) / 1e6
print(f"     bundle size {size_mb:.0f} MB, {sum(1 for _ in dist.rglob('*'))} entries")
stray = [p.name for p in dist.rglob("*") if p.suffix in (".db", ".log") or p.name in ("app_settings.json", ".venv")]
check("bundle carries no user data", not stray, str(stray[:5]))
leaks = []
needle_user = os.environ.get("USERNAME", "").lower()
for p in list((REPO / "packaging").glob("*")) + [REPO / "scripts/build_package.py"]:
    if p.is_file():
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        if (needle_user and f"\\{needle_user}\\" in text) or "c:\\users" in text or "c:/users" in text:
            leaks.append(p.name)
check("spec, launchers and build script hold no absolute user path", not leaks, str(leaks))

# --- 2. Frozen CLI vs development CLI ---------------------------------------
pack = work / "pack"
subprocess.run([str(VENV_PY), str(HERE / "build_pack.py"), str(pack)], check=True, capture_output=True)
r = frozen("--help")
check("cli --help exits 0", r.returncode == 0, r.stderr[-300:])
for cmd in (["scan", pack, "--json"], ["scan", pack], ["analyze", pack, "--json"],
            ["crash", "analyze", HERE / "crash_forge.txt", "--pack", pack, "--json"],
            ["crash", "analyze", HERE / "crash_none.txt", "--json"]):
    a, b = frozen(*cmd), dev(*cmd)
    label = " ".join(str(c) if not isinstance(c, Path) else c.name for c in cmd)
    check(f"frozen == dev: {label}", a.returncode == b.returncode and a.stdout == b.stdout and bool(a.stdout.strip()),
          f"rc {a.returncode}/{b.returncode} stdout {len(a.stdout)}/{len(b.stdout)} err={a.stderr[-200:]}")

# --- 3. End-to-end on the frozen CLI: profile -> scan -> crash -> report -> backup -> restore
data = work / "data"
dd = ["--data-dir", data]
steps = [
    ("profile add", ["profile", "add", "E2E Pack", pack, *dd]),
    ("profile scan", ["profile", "scan", "E2E Pack", "--label", "first", *dd]),
    ("crash analyze --save", ["crash", "analyze", HERE / "crash_forge.txt", "--profile", "E2E Pack", "--save", *dd]),
    ("report json", ["report", "E2E Pack", "--format", "json", "--out", work / "r_frozen.json", "--force", *dd]),
    ("report html", ["report", "E2E Pack", "--format", "html", "--out", work / "r_frozen.html", "--force", *dd]),
    ("backup create", ["backup", "create", "--out", work / "b.zip", "--force", *dd]),
    ("backup verify", ["backup", "verify", work / "b.zip", *dd]),
]
for label, cmd in steps:
    r = frozen(*cmd)
    check(f"frozen e2e: {label}", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("frozen e2e: database created in the requested data dir", (data / "mmis.db").is_file())
r = dev("report", "E2E Pack", "--format", "json", "--out", work / "r_dev.json", "--force", *dd)
same = r.returncode == 0 and (work / "r_dev.json").read_bytes() == (work / "r_frozen.json").read_bytes()
check("frozen e2e: JSON report byte-identical to the development CLI", same, r.stderr[-200:])
html = (work / "r_frozen.html").read_text(encoding="utf-8") if (work / "r_frozen.html").exists() else ""
check("frozen e2e: HTML report is self-contained", "<html" in html.lower() and "http://" not in html and "https://" not in html.replace("https://www.w3.org", ""))

# state change after the backup, then restore must bring the old state back
r = frozen("profile", "add", "After Backup", pack, *dd)
listed = frozen("profile", "list", "--json", *dd).stdout
check("frozen e2e: profile added after the backup is listed", r.returncode == 0 and "After Backup" in listed)
tampered = work / "b_tampered.zip"
with zipfile.ZipFile(work / "b.zip") as zin, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        blob = zin.read(item.filename)
        if item.filename == "mmis.db":
            blob = blob[:-16] + b"\0" * 16
        zout.writestr(item, blob)
r = frozen("backup", "verify", tampered, *dd)
check("frozen e2e: tampered backup refused by verify", r.returncode != 0, r.stdout[-200:])
r = frozen("backup", "restore", tampered, "--yes", *dd)
still = frozen("profile", "list", "--json", *dd).stdout
check("frozen e2e: tampered backup refused by restore, data intact", r.returncode != 0 and "After Backup" in still, r.stdout[-200:])
r = frozen("backup", "restore", work / "b.zip", "--yes", *dd)
after = frozen("profile", "list", "--json", *dd).stdout
check("frozen e2e: valid restore succeeds", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("frozen e2e: restore brought back the backed-up state", "E2E Pack" in after and "After Backup" not in after, after[:200])
safety = list(data.rglob("mmis_pre_restore_*.zip")) + list(work.glob("mmis_pre_restore_*.zip"))
check("frozen e2e: automatic safety backup written", bool(safety))
r = frozen("crash", "list", "--json", *dd)
check("frozen e2e: saved crash survives the restore", r.returncode == 0 and "crash_forge" in r.stdout, r.stdout[:200])

# --- 4. GUI executable ------------------------------------------------------
user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pid):
    found = []

    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left > 400:
                found.append((buf.value, rect.right - rect.left, rect.bottom - rect.top))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def run_gui(tag, args, env_extra):
    env = dict(CLEAN_ENV, MMIS_AUTO_CLOSE_MS="7000", **env_extra)
    t0 = time.monotonic()
    proc = subprocess.Popen([str(GUI), *map(str, args)], env=env, cwd=str(work), stdin=subprocess.DEVNULL)
    seen = []
    while proc.poll() is None and time.monotonic() - t0 < 90:
        if not seen:
            seen = windows_of(proc.pid)
            if seen:
                print(f"     {tag}: window {seen[0]} after {time.monotonic() - t0:.1f} s")
        time.sleep(0.2)
    if proc.poll() is None:
        proc.kill()
        check(f"{tag}: exits by itself", False, "still running after 90 s")
        return
    check(f"{tag}: a visible main window appeared", bool(seen))
    check(f"{tag}: exit code 0", proc.returncode == 0, f"rc={proc.returncode}")


run_gui("gui --data-dir", ["--data-dir", data], {})
logs = [p for p in data.rglob("*.log")]
trace = [p.name for p in logs if "Traceback" in p.read_text(encoding="utf-8", errors="replace")]
check("gui --data-dir: no traceback in the UI log", not trace, str(trace))
run_gui("gui default data dir", [], {})
check("gui default data dir: uses %APPDATA%\\MMIS of its environment", (fake_appdata / "MMIS" / "mmis.db").is_file())
run_gui("gui via MMIS_DATA_DIR", [], {"MMIS_DATA_DIR": str(work / "envdata")})
check("gui via MMIS_DATA_DIR: database created there", (work / "envdata" / "mmis.db").is_file())

# --- 5. Side effects -------------------------------------------------------
check("real %APPDATA%\\MMIS untouched", appdata_fingerprint() == before_real)
left = [p.name for p in work.iterdir() if p.name not in {"appdata", "home", "pack", "data", "envdata", "r_frozen.json", "r_frozen.html", "r_dev.json", "b.zip", "b_tampered.zip"}]
check("no unexpected files written to the cwd", not [n for n in left if not n.startswith("mmis_pre_restore_")], str(left))

print("FAILED:", failed)
sys.exit(1 if failed else 0)
