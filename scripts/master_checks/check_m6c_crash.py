"""Master acceptance check for M6C-1 (Crash Logs page, crash_view, bounded import).

Usage: check_m6c_crash.py <seeded_data_dir> <work_dir>   (project venv python, PYTHONPATH=src, cwd = repo)

<seeded_data_dir> must be a FRESH dir from scripts/seed_demo_data.py: this check imports and
deletes crash reports in it. <work_dir> receives generated inputs and native grabs.
"""
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["MMIS_NO_ANIM"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

import mmis.services.profiles as profiles_mod
from mmis.analysis.crashlog import MAX_LOG_SIZE
from mmis.services import crash_view
from mmis.services.profiles import create_profile_service
from mmis.services.report import format_certainty
from mmis.ui import strings
from mmis.ui.app import create_app
from mmis.ui.pages.issues import CertaintyBadge
from mmis.ui.shell import MainWindow

from _geom import check, failed, pump, report_geometry, report_stretch, visible_text, wait_until

HERE = Path(__file__).resolve().parent
data_dir, work = Path(sys.argv[1]), Path(sys.argv[2])
if work.exists():
    shutil.rmtree(work)
work.mkdir(parents=True)
PROFILE, OTHER = "Demo Modpack", "Clean Modpack"
FORGE, FABRIC = HERE / "crash_forge.txt", HERE / "crash_fabric_dep.txt"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def badge_text(badge):
    texts = [str(l.text()) for l in badge.findChildren(QLabel) if str(l.text()).strip()]
    return texts[-1] if texts else ""


def rows(panel, object_name):
    return [w for w in panel.findChildren(QWidget) if w.objectName() == object_name and w.isVisible()]


def row_badges(panel, object_name):
    out = []
    for r in rows(panel, object_name):
        b = r.findChildren(CertaintyBadge)
        out.append(badge_text(b[0]) if len(b) == 1 else f"<{len(b)} badges>")
    return out


service = create_profile_service(data_dir)

# ---------------------------------------------------------------- 1. bounded read (service level)
big = work / "huge.log"
line = b"[12:00:00] [Server thread/INFO]: filler line that carries no diagnostic value at all\n"
with open(big, "wb") as fh:
    block = line * 12000  # ~1 MB
    for _ in range(200):
        fh.write(block)
big_size, big_sha = big.stat().st_size, sha(big)
seen = []
orig_analyze = profiles_mod.analyze_crash


def spy(data, *a, **k):
    seen.append(len(data))
    return orig_analyze(data, *a, **k)


profiles_mod.analyze_crash = spy
t0 = time.monotonic()
try:
    rec = service.import_crash(big, profile_name=None)
finally:
    profiles_mod.analyze_crash = orig_analyze
check("bounded-read: analyzer receives at most MAX_LOG_SIZE+1 bytes", bool(seen) and max(seen) <= MAX_LOG_SIZE + 1, f"passed {seen} bytes")
check("bounded-read: file_size is the on-disk size", rec.file_size == big_size, f"{rec.file_size} != {big_size}")
check("bounded-read: sha256 covers the whole file", rec.sha256 == big_sha)
check("bounded-read: stored analysis flagged truncated", service.get_crash(rec.id)[1].is_truncated)
print(f"     200 MB import took {time.monotonic() - t0:.1f}s")
service.delete_crash(rec.id)
big.unlink()

# ---------------------------------------------------------------- 2. crash_view (no Qt)
r_unlinked = service.import_crash(FABRIC.read_bytes(), profile_name=None, source_name="unlinked_fabric.txt")
r_other = service.import_crash(FABRIC.read_bytes(), profile_name=OTHER, source_name="other_fabric.txt")
ids_all = [c.id for c in crash_view.list_crashes(service, None)]
ids_demo = [c.id for c in crash_view.list_crashes(service, PROFILE)]
check("crash_view: 'all' lists unlinked and other-profile crashes", r_unlinked.id in ids_all and r_other.id in ids_all)
check("crash_view: profile scope hides unlinked and other-profile crashes", r_unlinked.id not in ids_demo and r_other.id not in ids_demo)
unl = next((c for c in crash_view.list_crashes(service, None) if c.id == r_unlinked.id), None)
check("crash_view: unlinked crash has no profile name", unl is not None and unl.profile_name is None)
det = crash_view.get_crash_detail(service, r_other.id)
_, ana = service.get_crash(r_other.id)
check("crash_view: suspects keep engine order and certainty",
      [(s.mod_id, s.certainty) for s in det.suspects] == [(s.mod_id, format_certainty(s.certainty)) for s in ana.suspects])
check("crash_view: findings keep engine certainty",
      [(f.code, f.certainty) for f in det.findings] == [(f.code, format_certainty(f.certainty)) for f in ana.findings])
check("crash_view: find_duplicate matches sha + profile", crash_view.find_duplicate(service, r_other.sha256, OTHER) == r_other.id)
check("crash_view: find_duplicate is profile-scoped", crash_view.find_duplicate(service, r_other.sha256, PROFILE) is None)

# ---------------------------------------------------------------- 3. page behaviour
app = create_app()
win = MainWindow(service=service, data_dir=data_dir)
win.resize(1440, 900)
win.show()
pump(app)
win.set_active_profile(PROFILE)
dash = win.dashboard_page
check("record loads", wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card) is not None)
win.switch_page("crashes")
page = win.crashes_page
pump(app, 200)

events = []
page.import_finished.connect(lambda cid, dup: events.append(("ok", cid, dup)))
page.import_failed.connect(lambda code, msg: events.append(("fail", code, msg)))


def do_import(path, timeout_s=40.0):
    events.clear()
    page.import_file(str(path))
    t = wait_until(app, lambda: bool(events), timeout_s)
    pump(app, 150)
    return (events[0] if events else None), t


page.set_scope("active")
pump(app)
base_ids = list(page.visible_crash_ids())
forge_sha_before = sha(FORGE)

ev, _ = do_import(FORGE)
check("import: forge crash finishes", ev is not None and ev[0] == "ok", f"event={ev}")
forge_id = ev[1] if ev and ev[0] == "ok" else None
if forge_id is not None:
    rec, ana = service.get_crash(forge_id)
    demo_id = service.get_profile(PROFILE).id
    check("import: linked to the active profile", rec.profile_id == demo_id)
    check("import: not flagged duplicate", ev[2] is False)
    check("import: new crash listed and selected", forge_id in page.visible_crash_ids() and page.selected_crash_id() == forge_id)
    check("import: source file untouched", sha(FORGE) == forge_sha_before)
    panel = page.detail_panel
    txt = visible_text(panel)
    for label, value in (("mc version", ana.mc_version), ("loader", ana.detected_loader),
                         ("root cause class", ana.root_cause.exception_class if ana.root_cause else None),
                         ("source name", rec.source_name)):
        if value:
            check(f"detail: shows {label}", str(value).lower() in txt.lower(), f"'{value}' not in visible text")
    check("detail: one row per engine suspect", len(rows(panel, "crashSuspectRow")) == len(ana.suspects),
          f"{len(rows(panel, 'crashSuspectRow'))} rows vs {len(ana.suspects)} suspects")
    check("detail: suspect certainty badges match engine", row_badges(panel, "crashSuspectRow") == [format_certainty(s.certainty) for s in ana.suspects],
          f"{row_badges(panel, 'crashSuspectRow')}")
    for s in ana.suspects:
        if s.mod_id:
            check(f"detail: suspect '{s.mod_id}' named", s.mod_id in txt)
    check("detail: one row per engine finding", len(rows(panel, "crashFindingRow")) == len(ana.findings),
          f"{len(rows(panel, 'crashFindingRow'))} rows vs {len(ana.findings)} findings")
    check("detail: finding certainty badges match engine", row_badges(panel, "crashFindingRow") == [format_certainty(f.certainty) for f in ana.findings],
          f"{row_badges(panel, 'crashFindingRow')}")
    check("detail: 'original log not stored' notice visible", strings.CRASH_RAW_NOT_STORED_NOTICE in txt)
    check("detail: no truncation notice on a small log", strings.CRASH_TRUNCATED_NOTICE not in txt)

    n_before = len(service.list_crashes())
    ev, _ = do_import(FORGE)
    check("duplicate: reported as duplicate with the same id", ev is not None and ev[0] == "ok" and ev[1] == forge_id and ev[2] is True, f"event={ev}")
    check("duplicate: no second row stored", len(service.list_crashes()) == n_before)

# scope filter
page.refresh()
pump(app)
page.set_scope("active")
pump(app)
act = page.visible_crash_ids()
check("scope active: hides unlinked and other-profile crashes", r_unlinked.id not in act and r_other.id not in act, f"{act}")
page.set_scope("all")
pump(app)
allv = page.visible_crash_ids()
check("scope all: shows unlinked and other-profile crashes", r_unlinked.id in allv and r_other.id in allv, f"{allv}")
check("scope all: superset of active", set(act) <= set(allv))
all_txt = visible_text(page)
check("scope all: other profile's name shown in the list", OTHER in all_txt)
check("scope all: unlinked crashes labelled", strings.CRASH_UNLINKED_LABEL in all_txt)
page.set_scope("active")
pump(app)

# degenerate inputs
empty = work / "empty.log"
empty.write_bytes(b"")
n_before = len(service.list_crashes())
ev, _ = do_import(empty)
check("empty file: rejected with EMPTY_FILE", ev is not None and ev[0] == "fail" and ev[1] == "EMPTY_FILE", f"event={ev}")
check("empty file: nothing stored", len(service.list_crashes()) == n_before)
ev, _ = do_import(work / "does_not_exist.log")
check("missing file: rejected with FILE_NOT_FOUND", ev is not None and ev[0] == "fail" and ev[1] == "FILE_NOT_FOUND", f"event={ev}")
check("missing file: nothing stored", len(service.list_crashes()) == n_before)
ev, _ = do_import(work)
check("directory: rejected, nothing stored", ev is not None and ev[0] == "fail" and len(service.list_crashes()) == n_before, f"event={ev}")
check("page still importable after failures", not page.is_importing())

binf = work / "binary.bin"
binf.write_bytes(bytes(range(256)) * 400)
ev, _ = do_import(binf)
check("binary file: import finishes", ev is not None and ev[0] == "ok", f"event={ev}")
if ev and ev[0] == "ok":
    _, ana = service.get_crash(ev[1])
    panel = page.detail_panel
    txt = visible_text(panel)
    check("binary file: no suspects invented", len(ana.suspects) == 0 and len(rows(panel, "crashSuspectRow")) == 0)
    check("binary file: explicit 'no suspects' text", strings.CRASH_NO_SUSPECTS in txt)
    check("binary file: finding shown as engine reports it", row_badges(panel, "crashFindingRow") == [format_certainty(f.certainty) for f in ana.findings],
          f"{row_badges(panel, 'crashFindingRow')}")

# large log, UI responsiveness
large = work / "large_25mb.log"
with open(large, "wb") as fh:
    fh.write(FORGE.read_bytes())
    fh.write(b"\n")
    block = line * 12000
    while fh.tell() < 25 * 1024 * 1024:
        fh.write(block)
ticks = []
timer = QTimer()
timer.setInterval(50)
timer.timeout.connect(lambda: ticks.append(time.monotonic()))
timer.start()
ev, took = do_import(large, timeout_s=60.0)
timer.stop()
check("large log: import finishes", ev is not None and ev[0] == "ok", f"event={ev}")
check("large log: within 30 s", took is not None and took <= 30.0, f"took={took}")
gaps = [b - a for a, b in zip(ticks, ticks[1:])]
check("large log: UI thread stays responsive (max timer gap < 1 s)", len(ticks) >= 2 and max(gaps) < 1.0,
      f"ticks={len(ticks)} max gap={max(gaps) if gaps else None}")
print(f"     25 MB import took {took if took is None else round(took, 1)}s, {len(ticks)} ticks, max gap {round(max(gaps), 3) if gaps else None}s")
if ev and ev[0] == "ok":
    rec, ana = service.get_crash(ev[1])
    txt = visible_text(page.detail_panel)
    check("large log: engine flagged truncation", ana.is_truncated)
    check("large log: truncation notice visible", strings.CRASH_TRUNCATED_NOTICE in txt)
    check("large log: file_size is the on-disk size", rec.file_size == large.stat().st_size)
    large_id = ev[1]
else:
    large_id = None

# delete with confirmation
if large_id is not None:
    asked = []
    page.confirm_delete = lambda item: (asked.append(item), False)[1]
    page.request_delete(large_id)
    pump(app, 200)
    check("delete: confirmation requested", len(asked) == 1)
    check("delete: cancel keeps the crash", large_id in [c.id for c in service.list_crashes()] and large_id in page.visible_crash_ids())
    page.confirm_delete = lambda item: True
    page.request_delete(large_id)
    pump(app, 300)
    check("delete: confirm removes the crash", large_id not in [c.id for c in service.list_crashes()] and large_id not in page.visible_crash_ids())

# ---------------------------------------------------------------- 4. geometry + grabs at three sizes
for w_, h_ in ((1440, 900), (1200, 750), (960, 600)):
    tag = f"{w_}x{h_}"
    out = work / tag
    out.mkdir(parents=True, exist_ok=True)
    win.resize(w_, h_)
    pump(app, 250)
    win.switch_page("crashes")
    page.set_scope("all")
    if forge_id is not None:
        page.select_crash(forge_id)
    pump(app, 250)
    win.grab().save(str(out / "crashes-detail.png"))
    report_geometry(f"{tag} crashes", page)
    report_stretch(f"{tag} crashes", page)
    wide = []
    for cid in page.visible_crash_ids():
        page.select_crash(cid)
        pump(app, 150)
        for sa in page.findChildren(QScrollArea):
            if sa.isVisible() and sa.widget() is not None and sa.widget().width() > sa.viewport().width():
                wide.append((cid, sa.widget().width() - sa.viewport().width()))
    check(f"{tag} crashes: no horizontal overflow for any crash", not wide, f"(crash id, px over)={wide[:4]}")

# empty state
win.resize(1440, 900)
win.set_active_profile("Never Scanned Pack")
pump(app, 400)
win.switch_page("crashes")
page.set_scope("active")
pump(app, 250)
check("empty state: no crashes listed for a profile without crashes", page.visible_crash_ids() == [])
check("empty state: explicit empty text", strings.CRASH_EMPTY_TITLE in visible_text(page))
win.grab().save(str(work / "crashes-empty.png"))

win.close()
pump(app)
leftovers = [p.name for p in data_dir.iterdir() if p.name.startswith(".mmis_") or p.suffix in (".tmp", ".part")]
check("no temp files left in data dir", not leftovers, f"{leftovers}")

print("FAILED:", failed)
sys.exit(1 if failed else 0)
