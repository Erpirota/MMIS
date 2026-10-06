"""Master acceptance check for M6 part B (Issues + Compare + Dashboard layout).

Usage: check_m6b_ui.py <seeded_data_dir> <out_dir>   (project venv python, PYTHONPATH=src, cwd = repo)

The data dir must come from scripts/seed_demo_data.py. For each window size the
check waits for the async record load, then measures real widget geometry and
saves native grabs of the three pages to <out_dir>/<WxH>/.
"""
import os
import sys
import time
from pathlib import Path

os.environ["MMIS_NO_ANIM"] = "1"

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QLabel, QWidget

from mmis.services.profiles import create_profile_service
from mmis.ui import strings
from mmis.ui.app import create_app
from mmis.ui.pages.issues import CertaintyBadge, ElidedLabel, IssueCard
from mmis.ui.shell import MainWindow
from mmis.ui.widgets.status_pill import StatusPillWidget

data_dir, out_root = Path(sys.argv[1]), Path(sys.argv[2])
SIZES = [(1440, 900), (1200, 750), (960, 600)]
PROFILE = "Demo Modpack"
failed = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)


def pump(app, ms=150):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def is_card(w):
    return isinstance(w, QFrame) and (w.property("card") == "true" or w.property("card_elevated") == "true")


def inside_scroll_child(w, card):
    """True when w lives inside a nested scroll area of the card (legitimately clipped)."""
    p = w.parentWidget()
    while p is not None and p is not card:
        if isinstance(p, QAbstractScrollArea):
            return True
        p = p.parentWidget()
    return False


def geometry_problems(page):
    """Return (overlaps, escapes, clipped) for every visible card on the page."""
    cards = [w for w in page.findChildren(QFrame) if is_card(w) and w.isVisible()]
    overlaps, escapes, clipped = [], [], []
    by_parent = {}
    for c in cards:
        by_parent.setdefault(c.parentWidget(), []).append(c)
    for sibs in by_parent.values():
        sibs.sort(key=lambda c: (c.geometry().y(), c.geometry().x()))
        for a, b in zip(sibs, sibs[1:]):
            if a.geometry().intersects(b.geometry()):
                overlaps.append(f"{a.geometry().getRect()} x {b.geometry().getRect()}")
    for c in cards:
        bounds = c.rect()
        for w in c.findChildren(QWidget):
            if not w.isVisible() or inside_scroll_child(w, c):
                continue
            r = QRect(w.mapTo(c, QPoint(0, 0)), w.size())
            if not bounds.contains(r):
                escapes.append(f"{type(w).__name__} {r.getRect()} outside {bounds.getRect()}")
            # A child larger than its direct parent is painted cut off
            pw = w.parentWidget()
            if pw is not None and not pw.rect().contains(w.geometry()):
                escapes.append(f"{type(w).__name__} {w.geometry().getRect()} cut by parent {type(pw).__name__} {pw.rect().getRect()}")
            if isinstance(w, QLabel) and not w.wordWrap() and (w.text() if not isinstance(w, ElidedLabel) else "x"):
                if w.pixmap().isNull() and w.height() < w.fontMetrics().height():
                    clipped.append(f"'{str(w.text())[:30]}' h={w.height()} < {w.fontMetrics().height()}")
    return len(cards), overlaps, escapes, clipped


def report_geometry(tag, page):
    n, overlaps, escapes, clipped = geometry_problems(page)
    check(f"{tag}: has cards", n > 0, f"cards={n}")
    check(f"{tag}: no overlapping cards", not overlaps, f"{len(overlaps)} overlap(s), first: {overlaps[:1]}")
    check(f"{tag}: content inside cards", not escapes, f"{len(escapes)} escape(s), first: {escapes[:1]}")
    check(f"{tag}: labels not clipped", not clipped, f"{len(clipped)} clipped, first: {clipped[:1]}")


app = create_app()

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
    t0 = time.monotonic()
    dash = win.dashboard_page
    while dash._body_stack.currentWidget() is dash._state_card and time.monotonic() - t0 < 15:
        pump(app, 50)
    loaded = dash._body_stack.currentWidget() is not dash._state_card
    check(f"{tag}: record loads", loaded, f"still loading after {time.monotonic() - t0:.1f}s")
    print(f"     load time {int((time.monotonic() - t0) * 1000)} ms")
    pump(app, 200)

    # Dashboard
    win.switch_page("dashboard")
    pump(app)
    win.grab().save(str(out / "dashboard.png"))
    report_geometry(f"{tag} dashboard", dash)

    # Issues (collapsed)
    win.switch_page("issues")
    ip = win.issues_page
    ip.reset_filters()
    pump(app)
    win.grab().save(str(out / "issues.png"))
    report_geometry(f"{tag} issues", ip)
    cards = ip._issue_cards
    check(f"{tag} issues: cards present", len(cards) >= 5, f"n={len(cards)}")
    short = [c.height() for c in cards if c.height() < 48]
    check(f"{tag} issues: card height >= 48", not short, f"heights={sorted(set(short))}")
    step = [b.geometry().y() - a.geometry().y() for a, b in zip(cards, cards[1:])]
    print(f"     card heights={sorted({c.height() for c in cards})} y-steps={sorted(set(step))}")

    legend = [l for l in ip.findChildren(QLabel) if l.text() == strings.ISSUES_LEGEND_TEXT]
    if legend:
        lg = legend[0]
        need = lg.fontMetrics().horizontalAdvance(lg.text())
        fits = lg.wordWrap() and lg.height() >= lg.heightForWidth(lg.width()) or need <= lg.width()
        check(f"{tag} issues: legend fully visible", fits, f"text needs {need}px, label is {lg.width()}px, wordWrap={lg.wordWrap()}")
    else:
        print("     legend label not found as single QLabel (restructured) - verify on screenshot")

    badges = ip.findChildren(CertaintyBadge)
    boxed = [b for b in badges if "border" in b.styleSheet() and "{" not in b.styleSheet()]
    check(f"{tag} issues: certainty badge border scoped to badge", not boxed,
          f"{len(boxed)}/{len(badges)} badges use an unscoped stylesheet, border cascades to child labels")

    # Issues (first card with evidence expanded)
    target = next((c for c in cards if c.item.evidence), None)
    if target is not None:
        target.set_expanded(True)
        pump(app, 250)
        win.grab().save(str(out / "issues-expanded.png"))
        report_geometry(f"{tag} issues-expanded", ip)
        tbl = target._evidence_table
        rows = tbl.rowCount()
        need_h = tbl.horizontalHeader().height() + sum(tbl.rowHeight(r) for r in range(rows))
        shown = min(rows, 4)
        need_shown = tbl.horizontalHeader().height() + sum(tbl.rowHeight(r) for r in range(shown))
        check(f"{tag} issues: evidence table shows its rows", tbl.viewport().height() + tbl.horizontalHeader().height() >= need_shown,
              f"rows={rows} table h={tbl.height()} viewport h={tbl.viewport().height()} needs {need_shown} for {shown} row(s), {need_h} for all")
        cramped = []
        for r in range(rows):
            cw = tbl.cellWidget(r, 2)
            if cw is not None and tbl.rowHeight(r) < cw.sizeHint().height():
                cramped.append((r, tbl.rowHeight(r), cw.sizeHint().height()))
        check(f"{tag} issues: evidence rows tall enough for content", not cramped, f"(row, height, needed)={cramped[:3]}")
        stray = []
        for r in range(rows):
            cw, vr = tbl.cellWidget(r, 2), tbl.visualRect(tbl.model().index(r, 2))
            if cw is not None and not vr.contains(cw.geometry()):
                stray.append((r, cw.geometry().getRect(), vr.getRect()))
        check(f"{tag} issues: evidence cell widgets inside their rows", not stray, f"(row, cell, row rect)={stray[:2]}")
        target.set_expanded(False)
        pump(app)

    # Issues right after a rebuild: the first painted frame must already be laid out
    ip._apply_filters()
    app.processEvents()
    fresh = ip._issue_cards
    squeezed = sum(1 for a, b in zip(fresh, fresh[1:]) if a.geometry().intersects(b.geometry()))
    check(f"{tag} issues: no overlap on first frame after rebuild", squeezed == 0,
          f"{squeezed} overlapping pair(s), content h={ip._scroll_content.height()} viewport h={ip._scroll_area.viewport().height()}")
    pump(app, 200)

    # Compare
    win.switch_page("compare")
    cp = win.compare_page
    pump(app, 250)
    win.grab().save(str(out / "compare.png"))
    report_geometry(f"{tag} compare", cp)
    removed_rows = []
    for row in [f for f in cp.findChildren(QFrame) if is_card(f)]:
        texts = " ".join(str(l.text()) for l in row.findChildren(QLabel))
        pills = [p._status for p in row.findChildren(StatusPillWidget)]
        if "(was v" in texts and len(pills) == 1:
            removed_rows.append(pills[0])
    check(f"{tag} compare: removed mod row present", bool(removed_rows))
    check(f"{tag} compare: removed mod labelled 'removed'", bool(removed_rows) and all(s == "removed" for s in removed_rows), f"pills={removed_rows}")

    win.close()
    pump(app)

print("FAILED:", failed)
sys.exit(1 if failed else 0)
