"""Shared helpers for the Master UI acceptance checks (not a test module)."""
import time

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QLabel, QWidget

from mmis.ui.pages.issues import ElidedLabel, IssueCard

failed = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)
    return bool(cond)


def pump(app, ms=150):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, predicate, timeout_s=15.0):
    """Pump events until predicate() is true. Returns elapsed seconds, or None on timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if predicate():
            return time.monotonic() - t0
        app.processEvents()
        time.sleep(0.005)
    return None


def is_card(w):
    if isinstance(w, IssueCard):
        return True
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
    """Return (card_count, overlaps, escapes, clipped) for every visible card on the page."""
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


def visible_text(root):
    """All text of visible QLabels under root, joined (rich-text tags left in place)."""
    return "\n".join(str(l.text()) for l in root.findChildren(QLabel) if l.isVisible())


def stretched_labels(root, tolerance=40):
    """Visible text labels that are much taller than their text needs (blank bands inside cards)."""
    out = []
    for l in root.findChildren(QLabel):
        if not l.isVisible() or not str(l.text()).strip() or not l.pixmap().isNull():
            continue
        natural = l.heightForWidth(l.width()) if l.hasHeightForWidth() else l.sizeHint().height()
        if natural > 0 and l.height() > natural + tolerance:
            out.append(f"'{str(l.text())[:28]}' h={l.height()} needs {natural}")
    return out


def report_stretch(tag, root):
    s = stretched_labels(root)
    check(f"{tag}: no vertically stretched labels", not s, f"{len(s)} label(s), first: {s[:2]}")
