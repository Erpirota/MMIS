"""Geometric regression tests verifying layout constraints across screen resolutions.

Covers T1-T7 across 1440x900, 1200x750, and 960x600 resolutions on real MainWindow with seeded demo data.
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

SIZES = [(1440, 900), (1200, 750), (960, 600)]


@pytest.fixture(scope="module")
def seeded_data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("seeded_geo")
    data_dir = tmp / "data"
    pack_dir = tmp / "pack"
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo_data.py"
    subprocess.run(
        [sys.executable, str(script_path), "--data-dir", str(data_dir), "--pack-dir", str(pack_dir)],
        check=True,
        timeout=30,
    )
    return data_dir


def _pump(app, ms=150):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait_for_load(dash, app, timeout_s=10):
    t0 = time.monotonic()
    while dash._body_stack.currentWidget() is dash._state_card and time.monotonic() - t0 < timeout_s:
        _pump(app, 50)
    assert dash._body_stack.currentWidget() is not dash._state_card, "Record failed to load"


def _is_card(w):
    from PySide6.QtWidgets import QFrame
    return isinstance(w, QFrame) and (w.property("card") == "true" or w.property("card_elevated") == "true")


def _inside_scroll_child(w, card):
    from PySide6.QtWidgets import QAbstractScrollArea
    p = w.parentWidget()
    while p is not None and p is not card:
        if isinstance(p, QAbstractScrollArea):
            return True
        p = p.parentWidget()
    return False


def _geometry_problems(page):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QFrame, QLabel, QWidget
    from mmis.ui.pages.issues import ElidedLabel

    cards = [w for w in page.findChildren(QFrame) if _is_card(w) and w.isVisible()]
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
            if not w.isVisible() or _inside_scroll_child(w, c):
                continue
            r = QRect(w.mapTo(c, QPoint(0, 0)), w.size())
            if not bounds.contains(r):
                escapes.append(f"{type(w).__name__} {r.getRect()} outside {bounds.getRect()}")
            pw = w.parentWidget()
            if pw is not None and not pw.rect().contains(w.geometry()):
                escapes.append(f"{type(w).__name__} {w.geometry().getRect()} cut by parent {type(pw).__name__} {pw.rect().getRect()}")
            if isinstance(w, QLabel) and not w.wordWrap() and (w.text() if not isinstance(w, ElidedLabel) else "x"):
                if w.pixmap().isNull() and w.height() < w.fontMetrics().height():
                    clipped.append(f"'{str(w.text())[:30]}' h={w.height()} < {w.fontMetrics().height()}")
    return len(cards), overlaps, escapes, clipped


def _stretched_labels(root, tolerance=40):
    from PySide6.QtWidgets import QLabel
    out = []
    for l in root.findChildren(QLabel):
        if not l.isVisible() or not str(l.text()).strip() or not l.pixmap().isNull():
            continue
        natural = l.heightForWidth(l.width()) if l.hasHeightForWidth() else l.sizeHint().height()
        if natural > 0 and l.height() > natural + tolerance:
            out.append(f"'{str(l.text())[:28]}' h={l.height()} needs {natural}")
    return out


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_t1_t2_t3(qapp, seeded_data, width, height):
    """T1, T2, T3: Sibling cards do not intersect, content inside cards, labels not clipped."""
    from PySide6.QtWidgets import QLabel
    from mmis.services.profiles import create_profile_service
    from mmis.ui import strings
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    _wait_for_load(win.dashboard_page, qapp)

    for page_name in ("dashboard", "issues", "compare"):
        win.switch_page(page_name)
        _pump(qapp, 150)
        page = getattr(win, f"{page_name}_page")
        n_cards, overlaps, escapes, clipped = _geometry_problems(page)
        assert n_cards > 0, f"{page_name} has no cards at {width}x{height}"
        assert not overlaps, f"T1 failed on {page_name} at {width}x{height}: {overlaps}"
        assert not escapes, f"T2 failed on {page_name} at {width}x{height}: {escapes}"
        assert not clipped, f"T3 failed on {page_name} at {width}x{height}: {clipped}"

    # Also test with one issue card expanded
    win.switch_page("issues")
    ip = win.issues_page
    target = next((c for c in ip._issue_cards if c.item.evidence), None)
    if target:
        target.set_expanded(True)
        _pump(qapp, 150)
        n_cards, overlaps, escapes, clipped = _geometry_problems(ip)
        assert not overlaps, f"T1 failed on issues-expanded at {width}x{height}: {overlaps}"
        assert not escapes, f"T2 failed on issues-expanded at {width}x{height}: {escapes}"
        target.set_expanded(False)

    # T3: legend is fully visible
    legend_lbl = next((l for l in ip.findChildren(QLabel) if l.text() == strings.ISSUES_LEGEND_TEXT), None)
    assert legend_lbl is not None, "Legend label missing"
    assert legend_lbl.wordWrap() or legend_lbl.fontMetrics().horizontalAdvance(legend_lbl.text()) <= legend_lbl.width()

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_t4_profile_reload(qapp, seeded_data, width, height):
    """T4 (R2): Issues first frame after profile reload has zero overlapping cards."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.switch_page("issues")
    ip = win.issues_page

    # 1. Switch to Never Scanned Pack and settle
    win.set_active_profile("Never Scanned Pack")
    t_settle = time.monotonic()
    while ip.isVisible() and ip._active_record is not None and time.monotonic() - t_settle < 5:
        _pump(qapp, 20)
    _pump(qapp, 100)

    # 2. Hook set_data to detect the exact instant it finishes without extra settling pumps
    record_loaded = False
    orig_set_data = ip.set_data
    def _hooked_set_data(record):
        nonlocal record_loaded
        orig_set_data(record)
        record_loaded = True
    ip.set_data = _hooked_set_data

    win.set_active_profile("Demo Modpack")
    t0 = time.monotonic()
    while not record_loaded and time.monotonic() - t0 < 10:
        qapp.processEvents()
        time.sleep(0.002)

    assert record_loaded, "Record load did not complete"

    # Exactly one processEvents on the first frame
    qapp.processEvents()

    fresh = ip._issue_cards
    assert len(fresh) >= 5
    squeezed = sum(1 for a, b in zip(fresh, fresh[1:]) if a.geometry().intersects(b.geometry()))
    assert squeezed == 0, f"T4 overlap on first frame after profile reload: {squeezed} pairs intersecting"
    assert ip._scroll_content.height() >= sum(c.height() for c in fresh)

    # Optional additional check: _apply_filters variant
    ip._apply_filters()
    qapp.processEvents()
    squeezed_filter = sum(1 for a, b in zip(fresh, fresh[1:]) if a.geometry().intersects(b.geometry()))
    assert squeezed_filter == 0

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_t5_evidence_table(qapp, seeded_data, width, height):
    """T5 (R4 + R1): Evidence table shows up to 6 rows and cellWidget rect lies inside visualRect."""
    from PySide6.QtWidgets import QPushButton
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    _wait_for_load(win.dashboard_page, qapp)
    win.switch_page("issues")
    ip = win.issues_page
    _pump(qapp)

    target = next((c for c in ip._issue_cards if c.item.evidence), None)
    assert target is not None, "No card with evidence found"
    target.set_expanded(True)
    _pump(qapp, 150)
    win.grab()

    tbl = target._evidence_table
    rows = tbl.rowCount()
    shown = min(rows, 6)
    need_shown = tbl.horizontalHeader().height() + sum(tbl.rowHeight(r) for r in range(shown))
    assert tbl.viewport().height() + tbl.horizontalHeader().height() >= need_shown

    for r in range(rows):
        cw = tbl.cellWidget(r, 2)
        assert cw is not None
        assert tbl.rowHeight(r) >= cw.sizeHint().height()

        # R1: cell widget rect lies inside the row's visual rect
        v_rect = tbl.visualRect(tbl.model().index(r, 2))
        assert v_rect.contains(cw.geometry()), f"Row {r}: cell widget {cw.geometry().getRect()} outside visualRect {v_rect.getRect()}"

        btn = cw.findChild(QPushButton)
        if btn is not None:
            assert cw.rect().contains(btn.geometry()), f"Copy button escaped cell at row {r}"

    target.set_expanded(False)
    win.close()
    _pump(qapp, 50)


def test_layout_geometry_t5_synthetic_seven_evidence_rows(qapp):
    """R4: Synthetic IssueCard with 7 evidence rows: 6 fully visible, vertical scroll for 7th."""
    from PySide6.QtWidgets import QPushButton
    from mmis.services.issues_view import IssueEvidence, IssueItem
    from mmis.ui.pages.issues import IssueCard

    evidence_items = [
        IssueEvidence(file=f"test_{i}.jar", field=f"field_{i}", raw_value=f"value_{i}")
        for i in range(7)
    ]
    item = IssueItem(
        id="synth_7",
        code="TEST_7_ROWS",
        severity="error",
        certainty="Certain",
        category="metadata",
        message="Synthetic 7 evidence rows test",
        suggestion="Test fix",
        related_mods=["test_mod"],
        is_pack_level=False,
        evidence=evidence_items,
    )

    card = IssueCard(item)
    card.resize(600, 400)
    card.set_expanded(True)
    card.show()
    _pump(qapp, 100)
    card.grab()

    tbl = card._evidence_table
    assert tbl.rowCount() == 7

    hdr_h = tbl.horizontalHeader().height()
    h_6_rows = hdr_h + sum(tbl.rowHeight(r) for r in range(6))
    h_all_7 = hdr_h + sum(tbl.rowHeight(r) for r in range(7))

    # Exactly 6 rows fully visible in viewport + header
    assert tbl.viewport().height() + hdr_h >= h_6_rows
    # 7th row requires vertical scroll
    assert tbl.viewport().height() + hdr_h < h_all_7
    assert tbl.verticalScrollBar().maximum() > 0

    # Every cell widget rect lies inside the row's visual rect
    for r in range(7):
        cw = tbl.cellWidget(r, 2)
        assert cw is not None
        v_rect = tbl.visualRect(tbl.model().index(r, 2))
        assert v_rect.contains(cw.geometry()), f"Synthetic row {r}: cw {cw.geometry().getRect()} not in visualRect {v_rect.getRect()}"
        btn = cw.findChild(QPushButton)
        if btn is not None:
            assert cw.rect().contains(btn.geometry())

    card.close()


def test_layout_geometry_t6_certainty_badge_scoping(qapp):
    """T6: Certainty badge child labels have no border and stylesheet is strictly scoped."""
    from PySide6.QtWidgets import QLabel
    from mmis.ui.pages.issues import CertaintyBadge

    badge = CertaintyBadge("Certain")
    assert "{" in badge.styleSheet()
    assert "QFrame#certaintyBadge" in badge.styleSheet()
    for lbl in badge.findChildren(QLabel):
        assert "border: 1px" not in lbl.styleSheet()
    badge_possible = CertaintyBadge("Possible")
    assert "QFrame#certaintyBadge" in badge_possible.styleSheet()


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_t7_metric_ring_status_fit(qapp, seeded_data, width, height):
    """T7 (R3): Real MetricRing on Dashboard with Demo Modpack fits status text into inner chord (no fudge)."""
    from PySide6.QtGui import QFontMetrics
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    _wait_for_load(win.dashboard_page, qapp)

    win.switch_page("dashboard")
    _pump(qapp, 100)

    ring = win.dashboard_page._ring
    assert ring._status is not None, "MetricRing has no status set"

    status_rect, status_font, chord_width = ring.get_status_text_geometry()
    fm = QFontMetrics(status_font)
    text_width = fm.horizontalAdvance(ring._status)

    assert text_width <= chord_width, (
        f"Status text '{ring._status}' width {text_width}px exceeds inner chord {chord_width:.1f}px "
        f"at window size {width}x{height} (status_rect={status_rect.getRect()})"
    )

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_crashes_page_selected(qapp, seeded_data, width, height):
    """Crashes page geometry: no overlapping cards, content inside cards, no clipped labels across 1440, 1200, 960."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    win.switch_page("crashes")
    page = win.crashes_page
    page.set_scope("all")
    page.refresh()
    _pump(qapp, 150)

    v_ids = page.visible_crash_ids()
    assert len(v_ids) > 0, "Expected seeded crashes to be visible"
    page.select_crash(v_ids[0])
    _pump(qapp, 150)

    n, overlaps, escapes, clipped = _geometry_problems(page)
    assert n > 0, f"Expected cards on crashes page, got {n}"
    assert not overlaps, f"Overlapping cards on crashes page at {width}x{height}: {overlaps}"
    assert not escapes, f"Content outside cards on crashes page at {width}x{height}: {escapes}"
    assert not clipped, f"Labels clipped on crashes page at {width}x{height}: {clipped}"

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_crashes_page_no_horizontal_overflow_with_unbreakable_tokens(qapp, tmp_path: Path, width: int, height: int):
    """G1: Long unbreakable tokens (150 chars without spaces) wrap/break anywhere without horizontal scroll overflow."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea
    from mmis.services.profiles import create_profile_service
    from mmis.ui.pages.crashes import CrashesPage

    service = create_profile_service(tmp_path / f"data_{width}_{height}")
    token = "VeryLongUnbreakableSymbolWithoutAnySpaces" + "X" * 110  # 150+ chars
    crash_text = (
        f"Exception in thread \"main\" com.example.package.{token}: Failed to initialize mod\n"
        f"\tat com.example.{token}.m_123456_(SourceFile:42)\n"
    )
    rec = service.import_crash(crash_text.encode("utf-8"), profile_name=None, source_name="synthetic_unbreakable.txt")

    page = CrashesPage(service)
    page.set_scope("all")
    page.resize(width, height)
    page.show()
    _pump(qapp, 150)

    page.select_crash(rec.id)
    _pump(qapp, 150)

    for sa in page.findChildren(QScrollArea):
        if sa.isVisible() and sa.widget() is not None:
            assert sa.widget().width() <= sa.viewport().width(), (
                f"ScrollArea content width {sa.widget().width()} exceeded viewport width {sa.viewport().width()} "
                f"at {width}x{height}"
            )
            assert sa.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    page.close()
    _pump(qapp, 50)


def test_bounded_import_d4(tmp_path: Path):
    """D4: ProfileService.import_crash bounded read (at most MAX_LOG_SIZE+1 bytes to analyzer)."""
    import hashlib
    from mmis.analysis.crashlog import MAX_LOG_SIZE
    import mmis.services.profiles as profiles_mod
    from mmis.services.profiles import create_profile_service

    service = create_profile_service(tmp_path / "data")
    large_log = tmp_path / "large_test.log"
    line = b"[12:00:00] [Server thread/INFO]: sample test line for bounded read check\n"
    with open(large_log, "wb") as fh:
        block = line * 1000  # ~70 KB
        for _ in range(300):  # ~21 MB
            fh.write(block)

    real_size = large_log.stat().st_size
    h = hashlib.sha256()
    with open(large_log, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    real_sha = h.hexdigest()

    seen_lens: list[int] = []
    orig_analyze = profiles_mod.analyze_crash

    def spy(data, *args, **kwargs):
        seen_lens.append(len(data))
        return orig_analyze(data, *args, **kwargs)

    profiles_mod.analyze_crash = spy
    try:
        rec = service.import_crash(large_log, profile_name=None)
    finally:
        profiles_mod.analyze_crash = orig_analyze

    assert len(seen_lens) == 1
    assert seen_lens[0] <= MAX_LOG_SIZE + 1
    assert rec.file_size == real_size
    assert rec.sha256 == real_sha
    _, analysis = service.get_crash(rec.id)
    assert analysis.is_truncated is True


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_reports_page(qapp, seeded_data, width, height):
    """Reports page geometry across 1440, 1200, 960: no overlaps, escapes, clipping, no horizontal overflow."""
    from PySide6.QtWidgets import QScrollArea
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    _wait_for_load(win.dashboard_page, qapp)

    win.switch_page("reports")
    rp = win.reports_page
    t0 = time.monotonic()
    while rp.current_report() is None and time.monotonic() - t0 < 10:
        _pump(qapp, 50)
    assert rp.current_report() is not None, "Report failed to load"
    _pump(qapp, 150)

    n, overlaps, escapes, clipped = _geometry_problems(rp)
    assert n > 0, f"Expected cards on reports page, got {n}"
    assert not overlaps, f"Overlapping cards on reports page at {width}x{height}: {overlaps}"
    assert not escapes, f"Content outside cards on reports page at {width}x{height}: {escapes}"
    assert not clipped, f"Labels clipped on reports page at {width}x{height}: {clipped}"

    stretched = _stretched_labels(rp)
    assert not stretched, f"Vertically stretched labels on reports page at {width}x{height}: {stretched}"

    for sa in rp.findChildren(QScrollArea):
        if sa.isVisible() and sa.widget() is not None:
            assert sa.widget().width() <= sa.viewport().width(), (
                f"Reports scroll widget {sa.widget().width()} > viewport {sa.viewport().width()} at {width}x{height}"
            )

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_backup_page(qapp, seeded_data, width, height):
    """Backup page geometry across 1440, 1200, 960: no overlaps, escapes, clipping, no horizontal overflow."""
    from PySide6.QtWidgets import QScrollArea
    from mmis.services.backup import create_backup
    from mmis.services.backup_catalog import new_backup_path
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    # Ensure at least one backup exists to test populated card list
    bp = win.backup_page
    if not bp.visible_backup_paths():
        create_backup(seeded_data, new_backup_path(seeded_data))
        bp.refresh()

    win.switch_page("backup")
    _pump(qapp, 150)

    n, overlaps, escapes, clipped = _geometry_problems(bp)
    assert n > 0, f"Expected cards on backup page, got {n}"
    assert not overlaps, f"Overlapping cards on backup page at {width}x{height}: {overlaps}"
    assert not escapes, f"Content outside cards on backup page at {width}x{height}: {escapes}"
    assert not clipped, f"Labels clipped on backup page at {width}x{height}: {clipped}"

    stretched = _stretched_labels(bp)
    assert not stretched, f"Vertically stretched labels on backup page at {width}x{height}: {stretched}"

    for sa in bp.findChildren(QScrollArea):
        if sa.isVisible() and sa.widget() is not None:
            assert sa.widget().width() <= sa.viewport().width(), (
                f"Backup scroll widget {sa.widget().width()} > viewport {sa.viewport().width()} at {width}x{height}"
            )

    win.close()
    _pump(qapp, 50)


@pytest.mark.parametrize("width,height", SIZES)
def test_layout_geometry_crashes_page(qapp, seeded_data, width, height):
    """Crashes page geometry across 1440, 1200, 960: no overlaps, escapes, clipping, no horizontal overflow, no stretched labels."""
    from PySide6.QtWidgets import QFrame, QScrollArea
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    win = MainWindow(service=service, data_dir=seeded_data)
    win.resize(width, height)
    win.show()
    _pump(qapp)

    win.set_active_profile("Demo Modpack")
    win.switch_page("crashes")
    cp = win.crashes_page
    cp.set_scope("all")
    cp.refresh()
    _pump(qapp, 150)

    v_ids = cp.visible_crash_ids()
    assert len(v_ids) > 0, "No crashes visible"

    for cid in v_ids:
        cp.select_crash(cid)
        _pump(qapp, 100)

        n, overlaps, escapes, clipped = _geometry_problems(cp)
        assert n > 0, f"Expected cards on crashes page, got {n}"
        assert not overlaps, f"Overlapping cards on crashes page at {width}x{height}: {overlaps}"
        assert not escapes, f"Content outside cards on crashes page at {width}x{height}: {escapes}"
        assert not clipped, f"Labels clipped on crashes page at {width}x{height}: {clipped}"

        stretched = _stretched_labels(cp)
        assert not stretched, f"Vertically stretched labels on crashes page for crash {cid} at {width}x{height}: {stretched}"

        for sa in cp.findChildren(QScrollArea):
            if sa.isVisible() and sa.widget() is not None:
                assert sa.widget().width() <= sa.viewport().width(), (
                    f"Crashes scroll widget {sa.widget().width()} > viewport {sa.viewport().width()} at {width}x{height}"
                )

        cards = [w for w in cp.detail_panel.findChildren(QFrame) if _is_card(w) and w.isVisible()]
        if cards:
            last_card_bottom = max(c.geometry().bottom() for c in cards)
            panel_height = cp.detail_panel.height()
            assert abs(panel_height - (last_card_bottom + 1)) <= 1, (
                f"Detail panel height ({panel_height}) differs from last card bottom ({last_card_bottom + 1}) "
                f"by more than 1 px at {width}x{height}"
            )

    win.close()
    _pump(qapp, 50)


