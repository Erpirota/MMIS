"""State rendering tests for all pages (EMPTY, LOADING, READY, ERROR) (A7)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mmis.domain.models import ScanResult
from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.profiles import ProfileService


def test_dashboard_states(qapp):
    from mmis.ui import strings
    from mmis.ui.pages.dashboard import DashboardPage

    page = DashboardPage()
    page.show()

    # EMPTY
    page.show_empty_no_profiles()
    assert page._state_card.isVisible()
    assert strings.STATE_EMPTY_TITLE in page._state_card._title_label.text()
    assert not page._scroll_area.isVisible()

    # NEVER SCANNED
    page.show_never_scanned("TestPack")
    assert page._state_card.isVisible()
    assert "TestPack" in page._state_card._title_label.text()

    # LOADING
    page.show_loading()
    assert page._state_card.isVisible()
    assert strings.STATE_LOADING_TITLE in page._state_card._title_label.text()

    # ERROR
    page.show_error("PROFILE_PATH_MISSING", "Directory not found")
    assert page._state_card.isVisible()
    assert "PROFILE_PATH_MISSING" in page._state_card._code_label.text()

    # READY
    dummy_summary = ScanSummary(
        id=1, profile_id=1, scanned_at="2026-10-05T12:00:00Z", loader="fabric", mc_version="1.20.1",
        jar_count=2, mod_count=2, error_count=0, warning_count=0, info_count=0, label=None, pinned=False,
    )
    dummy_record = ScanRecord(
        summary=dummy_summary,
        result=ScanResult(scanned_path="/p", mods_dir="/p/mods"),
        analysis=None,
    )
    page.show_data(dummy_record)
    assert not page._state_card.isVisible()
    assert page._scroll_area.isVisible()

    page.close()


def test_mods_states(qapp):
    from mmis.ui import strings
    from mmis.ui.pages.mods import ModsPage

    page = ModsPage()
    page.show()

    # EMPTY
    page.show_empty_no_profiles()
    assert page._state_card.isVisible()
    assert strings.STATE_EMPTY_TITLE in page._state_card._title_label.text()

    # NEVER SCANNED
    page.show_never_scanned("NeverPack")
    assert page._state_card.isVisible()
    assert "NeverPack" in page._state_card._title_label.text()

    # LOADING
    page.show_loading()
    assert page._state_card.isVisible()

    # ERROR
    page.show_error("STORAGE_ERROR", "DB corrupted")
    assert page._state_card.isVisible()
    assert "STORAGE_ERROR" in page._state_card._code_label.text()

    page.close()


def test_profiles_states(qapp):
    from mmis.ui import strings
    from mmis.ui.pages.profiles import ProfilesPage

    mock_service = MagicMock(spec=ProfileService)
    mock_service.list_profiles.return_value = []

    page = ProfilesPage(service=mock_service)
    page.show()

    # Empty profiles list
    assert page._state_card.isVisible()
    assert strings.STATE_EMPTY_TITLE in page._state_card._title_label.text()

    page.close()
