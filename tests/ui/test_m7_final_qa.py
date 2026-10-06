"""Final QA automated tests covering Q1..Q5 defects (M7-5-FIX)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def seeded_data(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack"
    script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_demo_data.py"
    subprocess.run(
        [sys.executable, str(script_path), "--data-dir", str(data_dir), "--pack-dir", str(pack_dir)],
        check=True,
        timeout=30,
    )
    return data_dir


def test_q1_never_scanned_profile_empty_state(qapp, seeded_data: Path):
    """Q1: Selecting a never-scanned profile shows never-scanned empty state, not error."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui import strings
    from mmis.ui.shell import MainWindow
    from mmis.ui.widgets.state_card import PageState

    service = create_profile_service(seeded_data)
    window = MainWindow(service=service, data_dir=seeded_data)
    window.show()

    window.set_active_profile("Never Scanned Pack")
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    qapp.processEvents()

    assert not window.issues_page._state_card._code_label.isVisible()
    assert not window.dashboard_page._state_card._code_label.isVisible()
    assert not window.mods_page._state_card._code_label.isVisible()

    assert window.issues_page._state_card._desc_label.text() == strings.ISSUES_NEVER_SCANNED_DESC
    assert window.dashboard_page._state_card._desc_label.text() == strings.DASHBOARD_NEVER_SCANNED_DESC
    assert window.mods_page._state_card._desc_label.text() == strings.MODS_NEVER_SCANNED_DESC

    window.close()


@pytest.mark.parametrize("size", [(1440, 900), (1200, 750), (960, 600)])
@pytest.mark.parametrize("profile_name", ["Demo Modpack", "Clean Modpack"])
def test_q2_health_caption_not_clipped(qapp, seeded_data: Path, size: tuple[int, int], profile_name: str):
    """Q2: Caption under health ring is fully visible and inside parent card across sizes/profiles."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    window = MainWindow(service=service, data_dir=seeded_data)
    window.resize(*size)
    window.show()

    window.set_active_profile(profile_name)
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    qapp.processEvents()

    lbl = window.dashboard_page._ring_detail_label
    text = lbl.text()
    adv = lbl.fontMetrics().horizontalAdvance(text)
    hint_h = lbl.heightForWidth(lbl.width())

    # Caption is always fully visible: width >= advance, or word-wrapped with full height shown
    assert lbl.width() >= adv or (lbl.wordWrap() and lbl.height() >= hint_h)

    # Caption is inside its parent overview card
    parent_frame = window.dashboard_page._overview_frame
    lbl_pos = lbl.mapTo(parent_frame, lbl.rect().topLeft())
    assert lbl_pos.x() >= 0
    assert lbl_pos.y() >= 0
    assert lbl_pos.x() + lbl.width() <= parent_frame.width()
    assert lbl_pos.y() + lbl.height() <= parent_frame.height()

    # Ring size at 1440x900 is preserved
    if size == (1440, 900):
        ring = window.dashboard_page._ring
        assert ring.width() == 130
        assert ring.height() == 130

    window.close()


def test_q3_empty_findings_and_snapshots(qapp, seeded_data: Path):
    """Q3: Clean Modpack shows findings caption and 1 snapshot entry; Demo Modpack shows finding cards."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui import strings
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    window = MainWindow(service=service, data_dir=seeded_data)
    window.show()

    # Clean Modpack
    window.set_active_profile("Clean Modpack")
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    qapp.processEvents()

    dash = window.dashboard_page
    assert dash._issues_container.count() == 1
    item = dash._issues_container.itemAt(0).widget()
    assert item is not None
    assert item.property("caption") == "true"
    assert item.text() == strings.DASHBOARD_NO_FINDINGS

    assert dash._scans_container.count() == 1
    scan_item = dash._scans_container.itemAt(0).widget()
    assert scan_item is not None
    assert scan_item.property("card_elevated") == "true"

    # Demo Modpack
    window.set_active_profile("Demo Modpack")
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    qapp.processEvents()

    assert dash._issues_container.count() > 0
    first_issue = dash._issues_container.itemAt(0).widget()
    assert first_issue.property("card_elevated") == "true"

    window.close()


@pytest.mark.parametrize("size", [(1440, 900), (1200, 750), (960, 600)])
def test_q4_issues_header_not_stretched(qapp, seeded_data: Path, size: tuple[int, int]):
    """Q4: Issues header subtitle sits within 12 px under title in both clean and populated states."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    window = MainWindow(service=service, data_dir=seeded_data)
    window.resize(*size)
    window.show()

    # Clean Modpack (clean-empty state)
    window.set_active_profile("Clean Modpack")
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    window.switch_page("issues")
    qapp.processEvents()

    issues = window.issues_page
    title = issues._title_label
    subtitle = issues._subtitle_label
    gap_clean = subtitle.y() - (title.y() + title.height())
    assert gap_clean <= 12

    # Demo Modpack (populated state)
    window.set_active_profile("Demo Modpack")
    if window.task_manager.is_busy():
        window.task_manager.wait_for_all(5000)
    qapp.processEvents()

    gap_populated = subtitle.y() - (title.y() + title.height())
    assert gap_populated <= 12

    window.close()


def test_q5_profiles_page_refresh_no_overlap(qapp, seeded_data: Path):
    """Q5: Repeated refresh calls cleanly detach and hide cards without lingering overlapping children."""
    from mmis.services.profiles import create_profile_service
    from mmis.ui.pages.profiles import ProfileCard
    from mmis.ui.shell import MainWindow

    service = create_profile_service(seeded_data)
    window = MainWindow(service=service, data_dir=seeded_data)
    window.show()
    window.switch_page("profiles")
    qapp.processEvents()

    profiles_page = window.profiles_page
    num_profiles = len(service.list_profiles())

    for _ in range(3):
        profiles_page.refresh()
    qapp.processEvents()

    visible_cards = [
        c for c in profiles_page._cards_widget.findChildren(ProfileCard)
        if c.isVisible()
    ]
    assert len(visible_cards) == num_profiles

    for i in range(len(visible_cards)):
        for j in range(i + 1, len(visible_cards)):
            rect_i = visible_cards[i].geometry()
            rect_j = visible_cards[j].geometry()
            assert not rect_i.intersects(rect_j)

    window.close()
