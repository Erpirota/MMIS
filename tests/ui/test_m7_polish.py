"""Regression and polish verification tests for Task M7-2.

Covers:
- D1: Mods table header text elision, dynamic section size hints, and responsive scroll constraint across window sizes.
- D2: Dashboard health ring colour follows status word with score fallback.
- D3 / R2: Profiles page middle elision and viewport containment with 150-char and 200-char paths across window sizes.
- R1: Issues page on seeded demo data has zero horizontal overflow across window sizes.
- Unit tests for ElidedLabel sizeHint independence from text length.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def _make_demo_rows():
    from mmis.domain.models import ModInfo
    from mmis.services.pack_view import ModRow

    rows = []
    for i in range(15):
        rows.append(
            ModRow(
                mod_id=f"mod_{i}",
                name=f"Example Mod Name {i}",
                version=f"1.{i}.0",
                loader="Fabric" if i % 2 == 0 else "Forge",
                jar_path=f"mods/mod_{i}.jar",
                jar_name=f"mod_{i}.jar",
                is_disabled=False,
                is_nested=False,
                status="error" if i == 0 else ("warning" if i == 1 else "ok"),
                issue_count=2 if i == 0 else (1 if i == 1 else 0),
                error_count=2 if i == 0 else 0,
                warning_count=1 if i == 1 else 0,
                mod_info=ModInfo(id=f"mod_{i}", name=f"Example Mod Name {i}", version=f"1.{i}.0"),
            )
        )
    return rows


def test_d2_metric_ring_color_follows_status(qapp):
    """D2: MetricRing color strictly matches status word when set, falling back to score."""
    from mmis.ui.theme import tokens
    from mmis.ui.widgets.metric_ring import MetricRing

    ring = MetricRing(score=85, status="Errors")
    assert ring.get_color() == tokens.COLOR_ERROR

    ring.set_score(95, status="Warnings")
    assert ring.get_color() == tokens.COLOR_WARNING

    ring.set_score(40, status="Healthy")
    assert ring.get_color() == tokens.COLOR_SUCCESS

    # Fallback to score thresholds when status is None or unknown word
    ring.set_status(None)
    ring.set_score(90)
    assert ring.get_color() == tokens.COLOR_SUCCESS
    ring.set_score(70)
    assert ring.get_color() == tokens.COLOR_WARNING
    ring.set_score(40)
    assert ring.get_color() == tokens.COLOR_ERROR

    ring.set_status("UnknownStatus")
    ring.set_score(88)
    assert ring.get_color() == tokens.COLOR_SUCCESS
    ring.set_score(62)
    assert ring.get_color() == tokens.COLOR_WARNING
    ring.set_score(30)
    assert ring.get_color() == tokens.COLOR_ERROR


def test_d1_mods_table_header_not_elided_at_resolutions(qapp, tmp_path: Path):
    """D1: Header sectionSize >= sectionSizeHint for all visible columns at 1440, 1200, 960px, including after sorting."""
    from PySide6.QtCore import Qt
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow
    from mmis.ui.widgets.state_card import PageState

    service = create_profile_service(tmp_path)
    win = MainWindow(service=service, data_dir=tmp_path)
    win.show()
    win.switch_page("mods")
    page = win.mods_page

    page.set_state(PageState.READY)
    page._all_rows = _make_demo_rows()
    page.reset_filters()
    qapp.processEvents()

    sizes = [(1440, 900), (1200, 750), (960, 600)]
    for w, h in sizes:
        win.resize(w, h)
        qapp.processEvents()
        page.update_column_visibility()
        qapp.processEvents()

        header = page._table.horizontalHeader()

        # Columns 0 (Status) and 1 (Name) must always be visible
        assert not page._table.isColumnHidden(0), f"Status column hidden at {w}x{h}"
        assert not page._table.isColumnHidden(1), f"Name column hidden at {w}x{h}"

        # No horizontal scrollbar
        assert page._table.horizontalScrollBar().maximum() == 0, f"Horizontal scrollbar present at {w}x{h}"

        # Check sectionSize >= sectionSizeHint for all visible columns
        for col in range(header.count()):
            if not page._table.isColumnHidden(col):
                size = header.sectionSize(col)
                hint = header.sectionSizeHint(col)
                assert size >= hint, f"Column {col} elided at {w}x{h}: size {size} < hint {hint}"

        # Sort by each visible column and verify sectionSize >= sectionSizeHint and no horizontal scrollbar
        for sort_col in range(header.count()):
            if not page._table.isColumnHidden(sort_col):
                page._table.sortByColumn(sort_col, Qt.SortOrder.AscendingOrder)
                qapp.processEvents()

                assert page._table.horizontalScrollBar().maximum() == 0, f"Scrollbar after sort by {sort_col} at {w}x{h}"
                size = header.sectionSize(sort_col)
                hint = header.sectionSizeHint(sort_col)
                assert size >= hint, f"Sorted column {sort_col} elided at {w}x{h}: size {size} < hint {hint}"

    win.close()


def test_d3_r2_profiles_long_paths_no_overflow(qapp, tmp_path: Path):
    """D3 / R2: Both ~150-char and 200-char paths middle-elide with zero horizontal scrollbar across all window sizes."""
    from PySide6.QtCore import QPoint, QRect, Qt
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow
    from mmis.ui.widgets.elided_label import ElidedLabel

    service = create_profile_service(tmp_path)
    path_150 = "C:/Users/player/curseforge/minecraft/Instances/All The Mods 9 - To the Sky - ATM9TTS/saves/New World (1)/datapacks/really_long_nested_path_to_simulate_realistic"
    path_200 = "C:/Minecraft/Instances/" + "very_long_pack_directory_name/" * 6 + "instance_config"
    assert 140 <= len(path_150) <= 170
    assert len(path_200) >= 200

    service.add_profile("Profile 150 Char", path_150)
    service.add_profile("Profile 200 Char", path_200)

    win = MainWindow(service=service, data_dir=tmp_path)
    win.show()
    win.switch_page("profiles")
    page = win.profiles_page
    qapp.processEvents()

    for p_name in ("Profile 150 Char", "Profile 200 Char"):
        card = page._cards.get(p_name)
        assert card is not None
        assert isinstance(card._path_label, ElidedLabel)
        assert card._path_label.toolTip() == card.summary.path
        assert card._path_label.elide_mode() == Qt.TextElideMode.ElideMiddle

    sizes = [(1440, 900), (1200, 750), (960, 600)]
    for w, h in sizes:
        win.resize(w, h)
        qapp.processEvents()

        scroll_max = page._scroll_area.horizontalScrollBar().maximum()
        assert scroll_max == 0, f"Horizontal scrollbar present at {w}x{h}: max={scroll_max}"

        viewport = page._scroll_area.viewport()
        vp_r = viewport.rect()

        for p_name in ("Profile 150 Char", "Profile 200 Char"):
            card = page._cards[p_name]
            card_r = QRect(card.mapTo(viewport, QPoint(0, 0)), card.size())

            assert card_r.right() <= vp_r.right(), f"Card '{p_name}' right edge {card_r.right()} exceeds viewport {vp_r.right()} at {w}x{h}"

            for btn in (card._scan_btn, card._rename_btn, card._change_path_btn, card._delete_btn):
                assert btn.isVisible(), f"Button {btn} not visible on '{p_name}' at {w}x{h}"
                btn_r = QRect(btn.mapTo(card, QPoint(0, 0)), btn.size())
                assert card.rect().contains(btn_r), f"Button {btn} outside card '{p_name}' at {w}x{h}"

    win.close()


def test_r1_issues_page_no_horizontal_overflow_seeded_data(qapp, tmp_path: Path):
    """R1: Issues page on seeded demo data has zero horizontal overflow across 1440, 1200, and 960px."""
    import time
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack"
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo_data.py"
    subprocess.run(
        [sys.executable, str(script_path), "--data-dir", str(data_dir), "--pack-dir", str(pack_dir)],
        check=True,
        timeout=30,
    )

    service = create_profile_service(data_dir)
    win = MainWindow(service=service, data_dir=data_dir)
    win.show()
    win.set_active_profile("Demo Modpack")

    # Wait for async scan record load
    t0 = time.monotonic()
    while win.dashboard_page._body_stack.currentWidget() is win.dashboard_page._state_card and time.monotonic() - t0 < 10:
        qapp.processEvents()
        time.sleep(0.01)

    win.switch_page("issues")
    qapp.processEvents()

    sizes = [(1440, 900), (1200, 750), (960, 600)]
    for w, h in sizes:
        win.resize(w, h)
        qapp.processEvents()

        scroll_max = win.issues_page._scroll_area.horizontalScrollBar().maximum()
        assert scroll_max == 0, f"Issues page horizontal scrollbar present at {w}x{h}: max={scroll_max}"

    win.close()


def test_elided_label_size_hint_does_not_grow_with_text(qapp):
    """ElidedLabel unit test: sizeHint width does not grow with text, and painting does not alter width."""
    from mmis.ui.widgets.elided_label import ElidedLabel

    lbl = ElidedLabel("short")
    w1 = lbl.sizeHint().width()

    lbl.setText("a" * 1000)
    w2 = lbl.sizeHint().width()
    assert w1 == w2, f"sizeHint width grew with text: {w1} -> {w2}"

    lbl.resize(200, 30)
    lbl.show()
    qapp.processEvents()
    lbl.repaint()
    qapp.processEvents()
    assert lbl.width() == 200, f"Label width changed during paint: {lbl.width()}"
    lbl.close()
