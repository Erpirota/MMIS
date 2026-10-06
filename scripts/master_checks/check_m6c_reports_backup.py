"""Master acceptance check for M6C-2 (Reports page, Backup page, backup_catalog, safe restore).

Usage: check_m6c_reports_backup.py <seeded_data_dir> <work_dir>   (project venv python, PYTHONPATH=src, cwd = repo)

<seeded_data_dir> must be a FRESH dir from scripts/seed_demo_data.py: this check creates backups,
adds a profile and restores. <work_dir> receives exports, tampered archives and native grabs.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

os.environ["MMIS_NO_ANIM"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from mmis.domain.errors import BackupInvalidError
from mmis.services import backup_catalog
from mmis.services.backup import verify_backup
from mmis.services.profiles import create_profile_service
from mmis.services.report import build_diagnostic_report
from mmis.services.settings import get_settings_path
from mmis.ui import strings
from mmis.ui.app import create_app
from mmis.ui.shell import MainWindow

from _geom import check, failed, pump, report_geometry, report_stretch, visible_text, wait_until

data_dir, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
if work.exists():
    shutil.rmtree(work)
work.mkdir(parents=True)
PROFILE = "Demo Modpack"
SIZES = ((1440, 900), (1200, 750), (960, 600))
ENV = dict(os.environ, PYTHONHASHSEED="0")


def cli(*args):
    return subprocess.run([sys.executable, "-m", "mmis", "--data-dir", str(data_dir), *[str(a) for a in args]],
                          capture_output=True, text=True, env=ENV, stdin=subprocess.DEVNULL, timeout=120)


def db_fingerprint(db_path):
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        return hashlib.sha256("\n".join(conn.iterdump()).encode("utf-8")).hexdigest()
    finally:
        conn.close()


def state():
    """Logical state of the data dir: DB content, settings bytes, archives present, temp leftovers."""
    settings = get_settings_path(data_dir)
    zips = sorted((str(p.relative_to(data_dir)), p.stat().st_size) for p in data_dir.rglob("*.zip"))
    temps = sorted(p.name for p in data_dir.iterdir() if p.name.startswith(".mmis_"))
    return (db_fingerprint(data_dir / "mmis.db"), settings.read_bytes() if settings.is_file() else None, tuple(zips), tuple(temps))


def label_text(root, object_name):
    found = [w for w in root.findChildren(QLabel) if w.objectName() == object_name]
    return str(found[0].text()) if len(found) == 1 else f"<{len(found)} labels named {object_name}>"


def key(s):
    return str(s).strip().lower().replace(" ", "_")


def no_overflow(tag, page):
    wide = [(sa.widget().width() - sa.viewport().width()) for sa in page.findChildren(QScrollArea)
            if sa.isVisible() and sa.widget() is not None and sa.widget().width() > sa.viewport().width()]
    check(f"{tag}: no horizontal overflow", not wide, f"px over={wide}")


service = create_profile_service(data_dir)
app = create_app()
win = MainWindow(service=service, data_dir=data_dir)
win.resize(1440, 900)
win.show()
pump(app)
win.set_active_profile(PROFILE)
dash = win.dashboard_page
check("record loads", wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card) is not None)

# ================================================================= REPORTS
win.switch_page("reports")
rp = win.reports_page
check("reports: report built for the active profile", wait_until(app, lambda: rp.current_report() is not None) is not None)
pump(app, 200)
history = [s.id for s in service.get_history(PROFILE)]
check("reports: scan selector lists the profile's scans", list(rp.scan_ids()) == history, f"{rp.scan_ids()} vs {history}")
check("reports: latest scan selected by default", rp.selected_scan_id() == history[0])


def expected_report(scan_id):
    p = service.get_profile(PROFILE)
    return build_diagnostic_report(profile_name=p.name, scan_record=service.get_scan_record(PROFILE, scan_id),
                                   profile_path=p.path, latest_crash=service.get_latest_crash(PROFILE))


def check_preview(scan_id):
    exp = expected_report(scan_id)
    rep = rp.current_report()
    check(f"reports[{scan_id}]: preview model equals the service report", rep is not None and rep.to_dict() == exp.to_dict())
    for k, v in exp.severity_counts.items():
        check(f"reports[{scan_id}]: severity count '{k}' shown", label_text(rp, f"reportSeverityCount_{key(k)}") == str(v),
              f"{label_text(rp, f'reportSeverityCount_{key(k)}')} != {v}")
    for k, v in exp.certainty_counts.items():
        check(f"reports[{scan_id}]: certainty count '{k}' shown", label_text(rp, f"reportCertaintyCount_{key(k)}") == str(v),
              f"{label_text(rp, f'reportCertaintyCount_{key(k)}')} != {v}")
    recs = [w for w in rp.findChildren(QWidget) if w.objectName() == "reportRecommendationRow"]
    check(f"reports[{scan_id}]: one row per recommendation", len(recs) == len(exp.recommendations), f"{len(recs)} vs {len(exp.recommendations)}")
    txt = visible_text(rp)
    check(f"reports[{scan_id}]: status shown", exp.status.lower() in txt.lower(), f"'{exp.status}' not visible")
    if exp.latest_crash is not None:
        check(f"reports[{scan_id}]: latest crash source shown", exp.latest_crash.source_name in txt)


check_preview(history[0])

events = []
rp.export_finished.connect(lambda path: events.append(("ok", path)))
rp.export_failed.connect(lambda code, msg: events.append(("fail", code, msg)))


def do_export(fmt, out):
    events.clear()
    rp.export_report(fmt, str(out))
    wait_until(app, lambda: bool(events), 30.0)
    pump(app, 100)
    return events[0] if events else None


before = state()
for scan_id in history[:2]:
    rp.select_scan(scan_id)
    check(f"reports[{scan_id}]: report rebuilt on scan change",
          wait_until(app, lambda: rp.current_report() is not None and rp.current_report().scan_id == scan_id) is not None)
    pump(app, 150)
    if scan_id != history[0]:
        check_preview(scan_id)
    for fmt in ("html", "json"):
        ui1, ui2, ref = work / f"ui_{scan_id}.{fmt}", work / f"ui_{scan_id}_again.{fmt}", work / f"cli_{scan_id}.{fmt}"
        ev = do_export(fmt, ui1)
        check(f"export[{scan_id},{fmt}]: finishes", ev is not None and ev[0] == "ok" and ui1.is_file(), f"event={ev}")
        do_export(fmt, ui2)
        r = cli("report", PROFILE, "--scan", scan_id, "--format", fmt, "--out", ref)
        check(f"export[{scan_id},{fmt}]: CLI reference produced", r.returncode == 0 and ref.is_file(), r.stderr[:200])
        if ui1.is_file() and ui2.is_file() and ref.is_file():
            check(f"export[{scan_id},{fmt}]: byte-identical to the CLI export", ui1.read_bytes() == ref.read_bytes())
            check(f"export[{scan_id},{fmt}]: deterministic across two exports", ui1.read_bytes() == ui2.read_bytes())
check("export: data dir logically untouched", state() == before)

target = work / "existing.html"
target.write_bytes(b"KEEP ME")
asked = []
rp.confirm_overwrite = lambda path: (asked.append(path), False)[1]
ev = do_export("html", target)
check("export on existing file: confirmation requested", len(asked) == 1)
check("export on existing file: declined -> file untouched, CANCELLED", target.read_bytes() == b"KEEP ME" and ev is not None and ev[0] == "fail" and ev[1] == "CANCELLED", f"event={ev}")
rp.confirm_overwrite = lambda path: True
ev = do_export("html", target)
check("export on existing file: accepted -> overwritten", ev is not None and ev[0] == "ok" and target.read_bytes() != b"KEEP ME")
ev = do_export("html", work / "no_such_dir_file" / ".." / ".." / "\0bad")
check("export to an invalid path: reported as failure, no crash", ev is not None and ev[0] == "fail", f"event={ev}")

rp.select_scan(history[0])
wait_until(app, lambda: rp.current_report() is not None and rp.current_report().scan_id == history[0])
for w_, h_ in SIZES:
    win.resize(w_, h_)
    pump(app, 250)
    (work / f"{w_}x{h_}").mkdir(exist_ok=True)
    win.grab().save(str(work / f"{w_}x{h_}" / "reports.png"))
    report_geometry(f"{w_}x{h_} reports", rp)
    no_overflow(f"{w_}x{h_} reports", rp)
    report_stretch(f"{w_}x{h_} reports", rp)
win.resize(1440, 900)
pump(app, 200)

win.set_active_profile("Never Scanned Pack")
pump(app, 500)
win.switch_page("reports")
pump(app, 300)
check("reports empty state: no report for a never-scanned profile", rp.current_report() is None and list(rp.scan_ids()) == [])
check("reports empty state: explicit text", strings.REPORTS_EMPTY_TITLE in visible_text(rp))
win.grab().save(str(work / "reports-empty.png"))
win.set_active_profile(PROFILE)
wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card)
pump(app, 300)

# ================================================================= BACKUP
win.switch_page("backup")
bp = win.backup_page
pump(app, 300)
ops = []
bp.operation_finished.connect(lambda op, path: ops.append(("ok", op, path)))
bp.operation_failed.connect(lambda op, code, msg: ops.append(("fail", op, code, msg)))
opened = []
bp.open_folder = lambda p: opened.append(Path(p))
confirm_calls = []
bp.confirm_restore = lambda entry, manifest: (confirm_calls.append((entry, manifest)), True)[1]


def run_op(fn, *args, timeout_s=60.0):
    ops.clear()
    fn(*args)
    wait_until(app, lambda: bool(ops) and not bp.is_busy(), timeout_s)
    pump(app, 200)
    return ops[0] if ops else None


def norm(paths):
    return {str(Path(p).resolve()) for p in paths}


check("backup: empty state text when there are no backups", bp.visible_backup_paths() != [] or strings.BACKUP_EMPTY_TITLE in visible_text(bp))
win.grab().save(str(work / "backup-empty.png"))
names_a = sorted(p.name for p in service.list_profiles())

ev = run_op(bp.create_backup)
check("create: finishes", ev is not None and ev[:2] == ("ok", "create"), f"event={ev}")
backup_a = Path(ev[2]).resolve() if ev and ev[0] == "ok" else None
if backup_a is None:
    print("FAILED:", failed + ["cannot continue without a backup"])
    sys.exit(1)
check("create: stored in <data_dir>/backups", backup_a.parent == (data_dir / "backups").resolve(), str(backup_a))
check("create: listed on the page", str(backup_a) in norm(bp.visible_backup_paths()))
r = cli("backup", "verify", backup_a)
check("create: archive passes 'mmis backup verify'", r.returncode == 0, (r.stdout + r.stderr)[:200])
entries = backup_catalog.list_backups(data_dir)
ent = next((e for e in entries if Path(e.path).resolve() == backup_a), None)
check("catalog: entry for the new backup", ent is not None)
if ent is not None:
    check("catalog: manifest fields read", ent.manifest_ok and ent.kind == "manual" and ent.schema_version == 3 and bool(ent.created_at) and ent.size_bytes == backup_a.stat().st_size,
          f"{ent}")
check("status: not verified until asked", bp.verification_status(str(backup_a)) == "unverified", bp.verification_status(str(backup_a)))
ev = run_op(bp.verify_backup, str(backup_a))
check("verify: valid backup passes", ev is not None and ev[:2] == ("ok", "verify") and bp.verification_status(str(backup_a)) == "valid", f"event={ev}")
db_fp_a = None
with zipfile.ZipFile(backup_a) as zf:
    (work / "a_mmis.db").write_bytes(zf.read("mmis.db"))
db_fp_a = db_fingerprint(work / "a_mmis.db")

# ---- tampered archives (outside the data dir)
tdir = work / "tampered"
tdir.mkdir()
with zipfile.ZipFile(backup_a) as zf:
    members = {n: zf.read(n) for n in zf.namelist()}


def make(name, mutate):
    m = dict(members)
    extra = mutate(m) or {}
    p = tdir / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, b in m.items():
            zf.writestr(n, b)
        for n, b in extra.items():
            zf.writestr(n, b)
    return p


def bump_schema(m):
    man = json.loads(m["manifest.json"])
    man["schema_version"] = 99
    m["manifest.json"] = json.dumps(man, indent=2, sort_keys=True).encode("utf-8")


tampered = {
    "hash": make("hash.zip", lambda m: m.__setitem__("mmis.db", m["mmis.db"][:-1] + bytes([m["mmis.db"][-1] ^ 0xFF]))),
    "extra-member": make("extra.zip", lambda m: {"evil.txt": b"x"}),
    "unsafe-path": make("unsafe.zip", lambda m: {"../escape.txt": b"x"}),
    "schema-too-new": make("schema.zip", bump_schema),
    "no-manifest": make("nomanifest.zip", lambda m: m.pop("manifest.json") and None),
}
notzip = tdir / "notzip.zip"
notzip.write_bytes(b"this is not a zip archive at all")
tampered["not-a-zip"] = notzip
tampered["missing-file"] = tdir / "does_not_exist.zip"

for label, path in tampered.items():
    try:
        verify_backup(path)
        expected = None
    except BackupInvalidError as exc:
        expected = exc.reason
    check(f"tampered[{label}]: service rejects it", expected is not None)
    before = state()
    ev = run_op(bp.verify_backup, str(path))
    check(f"tampered[{label}]: page verify fails with {expected}", ev is not None and ev[:3] == ("fail", "verify", expected), f"event={ev}")
    check(f"tampered[{label}]: status invalid:{expected}", bp.verification_status(str(path)) == f"invalid:{expected}", bp.verification_status(str(path)))
    confirm_calls.clear()
    ev = run_op(bp.restore_backup, str(path))
    check(f"tampered[{label}]: restore refused with {expected}", ev is not None and ev[:3] == ("fail", "restore", expected), f"event={ev}")
    check(f"tampered[{label}]: user never asked to confirm an invalid archive", not confirm_calls)
    check(f"tampered[{label}]: data dir logically untouched", state() == before)
check("tampered: nothing escaped next to the data dir", not (data_dir.parent / "escape.txt").exists() and not (data_dir / "escape.txt").exists())
check("tampered: app still shows the same profiles", sorted(p.name for p in service.list_profiles()) == names_a)

# ---- unreadable file inside the managed folder must not break the listing
junk = data_dir / "backups" / "junk.zip"
junk.write_bytes(b"garbage")
bp.refresh()
pump(app, 200)
try:
    listed = backup_catalog.list_backups(data_dir)
    j = next((e for e in listed if Path(e.path).name == "junk.zip"), None)
    check("catalog: unreadable archive listed as not ok, no exception", j is not None and j.manifest_ok is False and bool(j.error), f"{j}")
except Exception as exc:  # noqa: BLE001
    check("catalog: unreadable archive listed as not ok, no exception", False, repr(exc))
check("page: unreadable archive listed, page alive", str(junk.resolve()) in norm(bp.visible_backup_paths()))
junk.unlink()
bp.refresh()
pump(app, 200)

# ---- diverge from state A, then exercise the refusals
(work / "tp" / "mods").mkdir(parents=True)
service.add_profile("Temp Profile", work / "tp")
names_b = sorted(p.name for p in service.list_profiles())
check("setup: state B differs from state A", names_b != names_a)

before = state()
confirm_calls.clear()
bp.confirm_restore = lambda entry, manifest: (confirm_calls.append((entry, manifest)), False)[1]
ev = run_op(bp.restore_backup, str(backup_a))
check("restore cancelled: confirmation asked once", len(confirm_calls) == 1)
if confirm_calls:
    man = confirm_calls[0][1]
    check("restore cancelled: confirmation received the manifest", isinstance(man, dict) and man.get("schema_version") == 3 and "created_at" in man, f"{man}")
check("restore cancelled: reported CANCELLED", ev is not None and ev[:3] == ("fail", "restore", "CANCELLED"), f"event={ev}")
check("restore cancelled: data dir logically untouched", state() == before)

bp.confirm_restore = lambda entry, manifest: (confirm_calls.append((entry, manifest)), True)[1]
confirm_calls.clear()
started = win.task_manager.start_scan(PROFILE)
ops.clear()
bp.restore_backup(str(backup_a))
busy_ev = ops[0] if ops else None
if busy_ev is None:
    wait_until(app, lambda: bool(ops), 5.0)
    busy_ev = ops[0] if ops else None
check("restore while scanning: refused with BUSY", started and busy_ev is not None and busy_ev[:3] == ("fail", "restore", "BUSY"), f"started={started} event={busy_ev}")
check("restore while scanning: no confirmation shown", not confirm_calls)
wait_until(app, lambda: not win.task_manager.is_scanning(), 30.0)
pump(app, 500)
check("restore while scanning: profiles still state B", sorted(p.name for p in service.list_profiles()) == names_b)

# ---- valid restore
zips_before = {p.name for p in data_dir.glob("mmis_pre_restore_*.zip")}
confirm_calls.clear()
ev = run_op(bp.restore_backup, str(backup_a), timeout_s=90.0)
check("restore: finishes", ev is not None and ev[:2] == ("ok", "restore"), f"event={ev}")
check("restore: confirmation asked once", len(confirm_calls) == 1)
check("restore: profiles are those of the backup", sorted(p.name for p in service.list_profiles()) == names_a)
check("restore: DB content equals the DB inside the backup", db_fingerprint(data_dir / "mmis.db") == db_fp_a)
new_safety = sorted({p.name for p in data_dir.glob("mmis_pre_restore_*.zip")} - zips_before)
check("restore: one safety backup created", len(new_safety) == 1, f"{new_safety}")
if new_safety:
    sp = data_dir / new_safety[0]
    try:
        verify_backup(sp)
        ok_safety = True
    except BackupInvalidError:
        ok_safety = False
    check("restore: safety backup is itself valid", ok_safety)
    check("restore: safety backup listed on the page", str(sp.resolve()) in norm(bp.visible_backup_paths()))
    se = next((e for e in backup_catalog.list_backups(data_dir) if Path(e.path).name == new_safety[0]), None)
    check("restore: safety backup catalogued as pre_restore", se is not None and se.kind == "pre_restore")
    with zipfile.ZipFile(sp) as zf:
        (work / "safety_mmis.db").write_bytes(zf.read("mmis.db"))
    c = sqlite3.connect(str(work / "safety_mmis.db"))
    try:
        check("restore: safety backup holds the pre-restore data", ("Temp Profile",) in c.execute("SELECT name FROM profiles").fetchall())
    finally:
        c.close()
check("restore: no temp dir left", not [p.name for p in data_dir.iterdir() if p.name.startswith(".mmis_")])
combo = win.topbar._profile_combo
ui_names = sorted(combo.itemText(i) for i in range(combo.count()))
check("restore: profile switcher refreshed without restart", ui_names == names_a, f"{ui_names}")
check("restore: window usable again", win.isEnabled() and win.sidebar.isEnabled() and win.stack.isEnabled() and not bp.is_busy())
alive = True
try:
    for pid in ("dashboard", "profiles", "mods", "issues", "compare", "crashes", "reports", "backup"):
        win.switch_page(pid)
        pump(app, 250)
except Exception as exc:  # noqa: BLE001
    alive = repr(exc)
check("restore: every page still renders", alive is True, str(alive))

# ---- page affordances
win.switch_page("backup")
pump(app, 200)
bp.open_backups_folder()
check("open folder: delegates to open_folder with the backups dir", len(opened) == 1 and opened[0].resolve() == (data_dir / "backups").resolve(), f"{opened}")
destructive = [b.text() for b in bp.findChildren(QPushButton) if any(k in b.text().lower() for k in ("delete", "remove", "elimina"))]
check("no delete action on the backup page", not destructive, f"{destructive}")

for w_, h_ in SIZES:
    win.resize(w_, h_)
    pump(app, 250)
    win.switch_page("backup")
    pump(app, 200)
    win.grab().save(str(work / f"{w_}x{h_}" / "backup.png"))
    report_geometry(f"{w_}x{h_} backup", bp)
    no_overflow(f"{w_}x{h_} backup", bp)
    report_stretch(f"{w_}x{h_} backup", bp)

win.close()
pump(app)
print("FAILED:", failed)
sys.exit(1 if failed else 0)
