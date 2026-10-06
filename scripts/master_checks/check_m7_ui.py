"""Master acceptance check for M7-2 (UI final polish).

Usage: check_m7_ui.py <seeded_data_dir> <out_dir>   (project venv python, PYTHONPATH=src, cwd = repo)

Checks, at three window sizes: Mods table headers are never elided, the
Dashboard ring colour agrees with the status word it shows, and every page
can be opened without horizontal overflow. Saves native grabs to <out_dir>.
"""
import os
import sys
from pathlib import Path

os.environ["MMIS_NO_ANIM"] = "1"

from PySide6.QtWidgets import QAbstractScrollArea, QHeaderView, QTableView

from _geom import check, failed, geometry_problems, pump, wait_until
from mmis.services.profiles import create_profile_service
from mmis.ui.app import create_app
from mmis.ui.shell import MainWindow
from mmis.ui.theme import tokens
from mmis.ui.widgets.metric_ring import MetricRing

data_dir, out_root = Path(sys.argv[1]), Path(sys.argv[2])
SIZES = [(1440, 900), (1200, 750), (960, 600)]
PAGES = ["dashboard", "profiles", "mods", "issues", "compare", "crashes", "reports", "backup"]
PROFILE = "Demo Modpack"
STATUS_COLOR = {"Errors": tokens.COLOR_ERROR, "Warnings": tokens.COLOR_WARNING, "Healthy": tokens.COLOR_SUCCESS}

app = create_app()

# --- Ring semantics, widget level --------------------------------------------
ring = MetricRing(100, label="x", status="Healthy")
for score in (100, 90, 70, 30):
    for status, colour in STATUS_COLOR.items():
        ring.set_score(score, status=status)
        check(f"ring {score}% '{status}': colour matches the status word", ring.get_color() == colour, f"got {ring.get_color()} want {colour}")
ring.set_status(None)
for score, colour in ((90, tokens.COLOR_SUCCESS), (70, tokens.COLOR_WARNING), (30, tokens.COLOR_ERROR)):
    ring.set_score(score)
    check(f"ring {score}% without status: colour still follows the score", ring.get_color() == colour, f"got {ring.get_color()}")

for w_, h_ in SIZES:
    tag = f"{w_}x{h_}"
    out = out_root / tag
    out.mkdir(parents=True, exist_ok=True)
    service = create_profile_service(data_dir)
    win = MainWindow(service=service, data_dir=data_dir)
    win.resize(w_, h_)
    win.show()
    pump(app)
    win.set_active_profile(PROFILE)
    dash = win.dashboard_page
    loaded = wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card)
    check(f"{tag}: record loads", loaded is not None)
    pump(app, 250)

    # Dashboard ring on the demo pack (which has error findings)
    win.switch_page("dashboard")
    pump(app)
    status = dash._ring._status
    check(f"{tag} dashboard: ring shows a known status", status in STATUS_COLOR, f"status={status!r}")
    check(f"{tag} dashboard: ring colour agrees with '{status}'", dash._ring.get_color() == STATUS_COLOR.get(status),
          f"colour={dash._ring.get_color()} score={dash._ring._score}")

    for page in PAGES:
        win.switch_page(page)
        pump(app, 300)
        win.grab().save(str(out / f"{page}.png"))
        widget = getattr(win, f"{page}_page")
        check(f"{tag} {page}: page is shown", widget.isVisible())
        over = []
        for area in widget.findChildren(QAbstractScrollArea):
            if not area.isVisible() or isinstance(area, QTableView):
                continue
            bar = area.horizontalScrollBar()
            if bar.maximum() > 0:
                over.append(f"{type(area).__name__} +{bar.maximum()}px")
        check(f"{tag} {page}: no horizontal overflow", not over, str(over[:2]))

    # Dashboard: the first frame after a record load must already be laid out
    win.switch_page("dashboard")
    win.set_active_profile("Clean Modpack")
    wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card)
    pump(app, 300)
    win.set_active_profile(PROFILE)
    first = wait_until(app, lambda: dash._body_stack.currentWidget() is not dash._state_card)
    n, overlaps, escapes, clipped = geometry_problems(dash)
    check(f"{tag} dashboard: first frame after load is laid out", first is not None and n > 0 and not (overlaps or escapes or clipped),
          f"cards={n} overlaps={len(overlaps)} escapes={len(escapes)} clipped={len(clipped)}")
    pump(app, 300)

    # Mods table headers
    win.switch_page("mods")
    pump(app, 300)
    tables = [t for t in win.mods_page.findChildren(QTableView) if t.isVisible()]
    check(f"{tag} mods: table visible", bool(tables))
    for t in tables[:1]:
        header: QHeaderView = t.horizontalHeader()
        model = t.model()
        elided = []
        for col in range(model.columnCount()):
            if header.isSectionHidden(col):
                continue
            need, have = header.sectionSizeHint(col), header.sectionSize(col)
            if have < need:
                elided.append(f"'{model.headerData(col, header.orientation())}' has {have}px needs {need}px")
        check(f"{tag} mods: no header text elided", not elided, "; ".join(elided))
        check(f"{tag} mods: Status and Name columns visible", not header.isSectionHidden(0) and not header.isSectionHidden(1))
        # Sorting on a column adds the sort arrow: the header must still fit
        t.sortByColumn(0, t.horizontalHeader().sortIndicatorOrder())
        pump(app, 100)
        need, have = header.sectionSizeHint(0), header.sectionSize(0)
        check(f"{tag} mods: Status header fits with the sort arrow", have >= need, f"has {have}px needs {need}px")
        total = sum(header.sectionSize(c) for c in range(model.columnCount()) if not header.isSectionHidden(c))
        check(f"{tag} mods: columns fit the table width", total <= t.viewport().width() + 2, f"columns {total}px viewport {t.viewport().width()}px")

    win.close()
    pump(app)

print("FAILED:", failed)
sys.exit(1 if failed else 0)
