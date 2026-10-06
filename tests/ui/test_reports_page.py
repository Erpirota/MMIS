"""UI integration tests for ReportsPage."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

import pytest

from mmis.services.profiles import create_profile_service


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


def _pump(app, ms=150):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait_until(app, predicate, timeout_s=10.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_reports_page_empty_state_no_profile(qapp, seeded_data: Path) -> None:
    from mmis.ui import strings
    from mmis.ui.pages.reports import ReportsPage

    service = create_profile_service(seeded_data)
    page = ReportsPage(service)
    page.set_active_profile(None)
    page.show()
    _pump(qapp, 50)

    assert page.current_report() is None
    assert list(page.scan_ids()) == []
    assert strings.REPORTS_EMPTY_TITLE in page._state_card._title_label.text()
    assert not page._export_html_btn.isEnabled()
    assert not page._export_json_btn.isEnabled()
    page.close()


def test_reports_page_empty_state_never_scanned(qapp, seeded_data: Path) -> None:
    from mmis.ui import strings
    from mmis.ui.pages.reports import ReportsPage

    service = create_profile_service(seeded_data)
    page = ReportsPage(service)
    page.set_active_profile("Never Scanned Pack")
    page.show()
    _pump(qapp, 50)

    assert page.current_report() is None
    assert list(page.scan_ids()) == []
    assert strings.REPORTS_EMPTY_TITLE in page._state_card._title_label.text()
    page.close()


def test_reports_page_populated_and_scan_change(qapp, seeded_data: Path) -> None:
    from PySide6.QtWidgets import QLabel
    from mmis.ui.pages.reports import ReportsPage

    service = create_profile_service(seeded_data)
    page = ReportsPage(service)
    page.show()
    page.set_active_profile("Demo Modpack")

    loaded = _wait_until(qapp, lambda: page.current_report() is not None, timeout_s=10.0)
    assert loaded, "Report failed to load for Demo Modpack"
    _pump(qapp, 100)

    report = page.current_report()
    assert report is not None
    assert len(page.scan_ids()) >= 2
    assert page.selected_scan_id() == page.scan_ids()[0]

    # Verify metric labels exist
    for sev, count in report.severity_counts.items():
        key = sev.strip().lower().replace(" ", "_")
        found = [w for w in page.findChildren(QLabel) if w.objectName() == f"reportSeverityCount_{key}"]
        assert len(found) == 1
        assert found[0].text() == str(count)

    for cert, count in report.certainty_counts.items():
        key = cert.strip().lower().replace(" ", "_")
        found = [w for w in page.findChildren(QLabel) if w.objectName() == f"reportCertaintyCount_{key}"]
        assert len(found) == 1
        assert found[0].text() == str(count)

    # Change scan
    second_scan = page.scan_ids()[1]
    page.select_scan(second_scan)
    _wait_until(qapp, lambda: page.current_report() is not None and page.current_report().scan_id == second_scan, 10.0)
    _pump(qapp, 100)

    assert page.current_report().scan_id == second_scan
    page.close()


def test_reports_page_export_flow(qapp, seeded_data: Path, tmp_path: Path) -> None:
    from mmis.ui.pages.reports import ReportsPage

    service = create_profile_service(seeded_data)
    page = ReportsPage(service)
    page.show()
    page.set_active_profile("Demo Modpack")

    _wait_until(qapp, lambda: page.current_report() is not None, timeout_s=10.0)
    _pump(qapp, 100)

    events: list[tuple[str, ...]] = []
    page.export_finished.connect(lambda p: events.append(("ok", p)))
    page.export_failed.connect(lambda c, m: events.append(("fail", c, m)))

    # Export HTML
    out_html = tmp_path / "report.html"
    page.export_report("html", str(out_html))
    _wait_until(qapp, lambda: bool(events), timeout_s=10.0)
    assert events and events[0] == ("ok", str(out_html))
    assert out_html.is_file()
    assert b"<!DOCTYPE html>" in out_html.read_bytes()

    # Overwrite confirmation declined
    events.clear()
    asked = []
    page.confirm_overwrite = lambda p: (asked.append(p), False)[1]
    page.export_report("html", str(out_html))
    _wait_until(qapp, lambda: bool(events), timeout_s=5.0)
    assert len(asked) == 1
    assert events[0][0] == "fail"
    assert events[0][1] == "CANCELLED"

    # Export JSON
    events.clear()
    out_json = tmp_path / "report.json"
    page.export_report("json", str(out_json))
    _wait_until(qapp, lambda: bool(events), timeout_s=10.0)
    assert events and events[0] == ("ok", str(out_json))
    assert out_json.is_file()

    page.close()
