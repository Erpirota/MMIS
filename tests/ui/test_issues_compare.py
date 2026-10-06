"""Comprehensive UI tests for Issues page, Compare page, and M5 polish backlog."""

from __future__ import annotations

from pathlib import Path

from mmis.domain.models import Certainty, Evidence, Finding, ModInfo, ScanResult, Severity
from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.storage.repository import ProfileRepository
from mmis.ui import strings


def test_pluralize_helper():
    assert strings.pluralize(1, "warning") == "1 warning"
    assert strings.pluralize(2, "warning") == "2 warnings"
    assert strings.pluralize(0, "warning") == "0 warnings"
    assert strings.pluralize(1, "error") == "1 error"
    assert strings.pluralize(3, "error") == "3 errors"
    assert strings.pluralize(1, "info", "info") == "1 info"
    assert strings.pluralize(5, "info", "info") == "5 info"


def test_metric_ring_percentage_and_status_separation(qapp):
    from mmis.ui.widgets.metric_ring import MetricRing

    ring = MetricRing(83, status="Errors")
    assert ring._score == 83
    # Label defaults to DASHBOARD_HEALTH_LABEL and status is "Errors"
    assert ring._label == strings.DASHBOARD_HEALTH_LABEL
    assert ring._status == "Errors"

    ring.set_score(95, status="Healthy")
    assert ring._score == 95
    assert ring._status == "Healthy"
    assert ring._label == strings.DASHBOARD_HEALTH_LABEL


def test_topbar_scan_button_exact_8px_gap(qapp):
    from mmis.ui.components.topbar import TopBar

    topbar = TopBar()
    topbar.show()

    scan_btn = topbar._scan_btn
    assert scan_btn._inner_layout.spacing() == 8
    topbar.close()


def test_mods_page_column_hiding_order_and_select_mod(qapp):
    from mmis.ui.pages.mods import ModsPage

    mods_page = ModsPage()
    mods_page.show()

    # Default wide width: all columns visible
    mods_page.update_column_visibility(1200)
    assert not mods_page._table.isColumnHidden(0)  # Status
    assert not mods_page._table.isColumnHidden(1)  # Name
    assert not mods_page._table.isColumnHidden(2)  # Mod ID
    assert not mods_page._table.isColumnHidden(4)  # Loader
    assert not mods_page._table.isColumnHidden(5)  # Issues
    assert not mods_page._table.isColumnHidden(6)  # Jar file

    # 1. Hides Jar file first (< 1050)
    mods_page.update_column_visibility(1000)
    assert mods_page._table.isColumnHidden(6)
    assert not mods_page._table.isColumnHidden(5)

    # 2. Hides Issues next (< 920)
    mods_page.update_column_visibility(850)
    assert mods_page._table.isColumnHidden(6)
    assert mods_page._table.isColumnHidden(5)
    assert not mods_page._table.isColumnHidden(4)

    # 3. Hides Loader next (< 800)
    mods_page.update_column_visibility(750)
    assert mods_page._table.isColumnHidden(6)
    assert mods_page._table.isColumnHidden(5)
    assert mods_page._table.isColumnHidden(4)
    assert not mods_page._table.isColumnHidden(2)

    # 4. Hides Mod ID next (< 680)
    mods_page.update_column_visibility(600)
    assert mods_page._table.isColumnHidden(6)
    assert mods_page._table.isColumnHidden(5)
    assert mods_page._table.isColumnHidden(4)
    assert mods_page._table.isColumnHidden(2)
    # Status (0) and Name (1) remain visible
    assert not mods_page._table.isColumnHidden(0)
    assert not mods_page._table.isColumnHidden(1)

    mods_page.close()


def _create_test_scan_record() -> ScanRecord:
    summary = ScanSummary(
        id=1,
        profile_id=1,
        scanned_at="2026-10-05T12:00:00Z",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=1,
        mod_count=1,
        error_count=1,
        warning_count=1,
        info_count=0,
    )
    f_err = Finding(
        code="DEP_MISSING_REQUIRED",
        message="Alpha requires Beta",
        severity=Severity.ERROR,
        certainty=Certainty.CERTAIN,
        related_mod_ids=["alpha"],
        evidence=[Evidence(file="alpha.jar", field="depends", raw_value="beta@>=1.0")],
        suggestion="Install Beta",
    )
    f_warn = Finding(
        code="CONFLICT_SOFT_DISCOURAGED",
        message="Alpha may conflict with Gamma",
        severity=Severity.WARNING,
        certainty=Certainty.POSSIBLE,
        related_mod_ids=["alpha", "gamma"],
        evidence=[Evidence(file="gamma.jar", field="mixins", raw_value="MixinRender")],
        suggestion="Check compatibility patch",
    )
    res = ScanResult(
        scanned_path="/test",
        mods_dir="/test/mods",
        jars=[],
        mods=[
            ModInfo(id="alpha", name="Alpha Mod", version="1.0.0", loaders=[], jar_path="/test/alpha.jar")
        ],
        findings=[f_err, f_warn],
    )
    return ScanRecord(summary=summary, result=res)


def test_issues_page_view_and_expansion(qapp):
    from mmis.ui.pages.issues import IssuesPage

    issues_page = IssuesPage()
    issues_page.show()

    record = _create_test_scan_record()
    issues_page.set_data(record)

    # Chips and counts
    assert issues_page._chip_error._count == 1
    assert issues_page._chip_warning._count == 1
    assert issues_page._chip_certain._count == 1
    assert issues_page._chip_possible._count == 1

    # Check cards
    assert len(issues_page._issue_cards) == 2
    card0 = issues_page._issue_cards[0]
    assert card0.item.code == "DEP_MISSING_REQUIRED"
    assert card0.item.certainty == "Certain"
    assert not card0.is_expanded()

    # Expand card
    card0.set_expanded(True)
    assert card0.is_expanded()
    assert card0._evidence_table.rowCount() == 1

    # Show in mods signal
    captured_mod: list[str] = []
    card0.show_in_mods_clicked.connect(lambda m: captured_mod.append(m))
    card0.show_in_mods_clicked.emit(card0.item.related_mods[0])
    assert captured_mod == ["alpha"]

    issues_page.close()


def test_issues_page_clean_pack_empty_state(qapp):
    from mmis.ui.pages.issues import IssuesPage

    issues_page = IssuesPage()
    issues_page.show()

    clean_summary = ScanSummary(
        id=2,
        profile_id=1,
        scanned_at="2026-10-05T12:00:00Z",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=1,
        mod_count=1,
        error_count=0,
        warning_count=0,
        info_count=0,
    )
    clean_res = ScanResult(scanned_path="/clean", mods_dir="/clean/mods", jars=[], mods=[], findings=[])
    clean_rec = ScanRecord(summary=clean_summary, result=clean_res)

    issues_page.set_data(clean_rec)
    assert issues_page._state_card.isVisible()
    assert strings.ISSUES_EMPTY_CLEAN_TITLE in issues_page._state_card._title_label.text()

    issues_page.close()


def test_compare_page_same_scan_hint_and_diff(qapp, tmp_path: Path):
    from mmis.ui.pages.compare import ComparePage

    db_path = get_db_path(tmp_path)
    repo = ProfileRepository(db_path)
    service = ProfileService(repo)

    # Add profile and two synthetic scans
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "mods").mkdir()
    p = service.add_profile("Compare Pack", pack_dir)

    scan1 = service.scan_profile(p.name, label="Scan 1")
    scan2 = service.scan_profile(p.name, label="Scan 2")

    compare_page = ComparePage(service)
    compare_page.show()
    compare_page.set_profile(p.name)

    assert compare_page._combo_a.count() == 2
    assert compare_page._combo_b.count() == 2

    # Different scans: same scan banner is hidden
    assert not compare_page._same_scan_banner.isVisible()

    # Same scan selected: same scan hint banner is visible
    compare_page._combo_a.setCurrentIndex(0)
    compare_page._combo_b.setCurrentIndex(0)
    assert compare_page._same_scan_banner.isVisible()

    # Pin toggle and label editing persistence
    scan_b_id = compare_page._combo_b.currentData()
    compare_page._label_input.setText("Custom Label")
    compare_page._save_scan_label()

    updated = service.get_history(p.name)
    target_scan = next(s for s in updated if s.id == scan_b_id)
    assert target_scan.label == "Custom Label"

    compare_page.close()


def test_shell_navigation_to_mod_from_issues(qapp, tmp_path: Path):
    from mmis.ui.shell import MainWindow

    db_path = get_db_path(tmp_path)
    repo = ProfileRepository(db_path)
    service = ProfileService(repo)

    pack_dir = tmp_path / "nav_pack"
    pack_dir.mkdir()
    (pack_dir / "mods").mkdir()
    p = service.add_profile("Nav Pack", pack_dir)

    window = MainWindow(service=service, data_dir=tmp_path)
    window.show()

    # Navigate to mod from issues
    window._on_navigate_to_mod("some_mod")
    assert window.stack.currentIndex() == 2  # switched to mods page

    window.close()


def test_scan_button_size_hint_and_no_truncation(qapp):
    from PySide6.QtGui import QFontMetrics
    from mmis.ui.components.topbar import ScanButton

    btn = ScanButton()
    btn.set_content(strings.TOPBAR_SCAN_BTN, "scan")
    fm = btn._text_lbl.fontMetrics()
    text_w = fm.horizontalAdvance(strings.TOPBAR_SCAN_BTN)
    expected_min_w = text_w + 16 + 8 + 28  # text + icon (16) + gap (8) + padding (28)

    assert btn.sizeHint().width() >= expected_min_w
    assert btn.minimumSizeHint().width() >= expected_min_w
    assert btn.text() == "Scan Pack"


def test_issues_row_height_constraints_at_different_resolutions(qapp):
    from mmis.ui.pages.issues import IssuesPage

    page = IssuesPage()
    record = _create_test_scan_record()
    page.set_data(record)

    # Test at 960x600
    page.resize(960, 600)
    page.show()
    qapp.processEvents()

    for card in page._issue_cards:
        assert card.height() >= 48
        assert card.height() >= card.sizeHint().height()

    # Test at 1440x900
    page.resize(1440, 900)
    qapp.processEvents()

    for card in page._issue_cards:
        assert card.height() >= 48
        assert card.height() >= card.sizeHint().height()

    page.close()


def test_shell_profile_switcher_synchronization(qapp, tmp_path: Path):
    from mmis.ui.shell import MainWindow

    db_path = get_db_path(tmp_path)
    repo = ProfileRepository(db_path)
    service = ProfileService(repo)

    p1_dir = tmp_path / "p1"
    p1_dir.mkdir()
    (p1_dir / "mods").mkdir()
    p1 = service.add_profile("Alpha Pack", p1_dir)

    p2_dir = tmp_path / "p2"
    p2_dir.mkdir()
    (p2_dir / "mods").mkdir()
    p2 = service.add_profile("Beta Pack", p2_dir)

    window = MainWindow(service=service, data_dir=tmp_path)
    window.show()
    qapp.processEvents()

    # Switch active profile programmatically
    window.set_active_profile("Beta Pack")
    assert window.topbar.current_profile() == "Beta Pack"
    assert window.topbar._profile_combo.currentText() == "Beta Pack"

    window.set_active_profile("Alpha Pack")
    assert window.topbar.current_profile() == "Alpha Pack"
    assert window.topbar._profile_combo.currentText() == "Alpha Pack"

    window.close()


def test_compare_chips_zero_count_and_formatting(qapp):
    from PySide6.QtWidgets import QLabel
    from mmis.ui.pages.compare import SummaryChip

    chip_zero = SummaryChip("Removed", 0, sign="-", kind="error")
    assert "Removed 0" in chip_zero.findChild(QLabel).text()

    chip_non_zero = SummaryChip("Removed", 2, sign="-", kind="error")
    assert "Removed -2" in chip_non_zero.findChild(QLabel).text()

    chip_added = SummaryChip("Added", 3, sign="+", kind="success")
    assert "Added +3" in chip_added.findChild(QLabel).text()
