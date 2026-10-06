"""UI tests for CrashesPage."""

from __future__ import annotations

from pathlib import Path
import time
import pytest

from mmis.services.profiles import create_profile_service


def _pump(qapp, ms=150):
    t_end = time.monotonic() + ms / 1000
    while time.monotonic() < t_end:
        qapp.processEvents()
        time.sleep(0.005)


def test_crashes_page_empty_state(qapp, tmp_path: Path):
    from mmis.ui.pages.crashes import CrashesPage
    from mmis.ui import strings

    service = create_profile_service(tmp_path / "data")
    page = CrashesPage(service)
    page.resize(800, 600)
    page.show()
    _pump(qapp, 50)

    assert page.visible_crash_ids() == []
    assert page.selected_crash_id() is None
    page.close()


def test_crashes_page_import_and_selection(qapp, tmp_path: Path):
    from mmis.ui.pages.crashes import CrashesPage

    service = create_profile_service(tmp_path / "data")
    p_dir = tmp_path / "pack"
    p_dir.mkdir()
    prof = service.add_profile("Test Pack", p_dir)

    log_file = tmp_path / "sample_crash.log"
    log_file.write_text("Exception in thread \"main\" java.lang.RuntimeException: Crash test\n\tat com.mod.Main.init(Main.java:10)", encoding="utf-8")

    page = CrashesPage(service)
    page.set_active_profile(prof.name)
    page.resize(1000, 700)
    page.show()
    _pump(qapp, 50)

    events: list[tuple[str, int, bool]] = []
    page.import_finished.connect(lambda cid, dup: events.append(("ok", cid, dup)))

    page.pick_file = lambda: str(log_file)
    page._on_import_button_clicked()

    t0 = time.monotonic()
    while not events and time.monotonic() - t0 < 10:
        _pump(qapp, 50)

    assert len(events) == 1
    status, cid, was_dup = events[0]
    assert status == "ok"
    assert was_dup is False
    assert page.selected_crash_id() == cid
    assert cid in page.visible_crash_ids()

    # Re-import same file -> duplicate
    events.clear()
    page._on_import_button_clicked()
    t0 = time.monotonic()
    while not events and time.monotonic() - t0 < 10:
        _pump(qapp, 50)

    assert len(events) == 1
    assert events[0] == ("ok", cid, True)

    page.close()


def test_crashes_page_degenerate_imports(qapp, tmp_path: Path):
    from mmis.ui.pages.crashes import CrashesPage

    service = create_profile_service(tmp_path / "data")
    page = CrashesPage(service)
    page.resize(800, 600)
    page.show()
    _pump(qapp, 50)

    fails: list[tuple[str, str]] = []
    page.import_failed.connect(lambda code, msg: fails.append((code, msg)))

    # Empty file
    empty_file = tmp_path / "empty.log"
    empty_file.write_bytes(b"")
    page.import_file(str(empty_file))
    t0 = time.monotonic()
    while not fails and time.monotonic() - t0 < 5:
        _pump(qapp, 50)
    assert len(fails) == 1 and fails[0][0] == "EMPTY_FILE"

    # Missing file
    fails.clear()
    page.import_file(str(tmp_path / "non_existent.log"))
    t0 = time.monotonic()
    while not fails and time.monotonic() - t0 < 5:
        _pump(qapp, 50)
    assert len(fails) == 1 and fails[0][0] == "FILE_NOT_FOUND"

    # Directory
    fails.clear()
    page.import_file(str(tmp_path))
    t0 = time.monotonic()
    while not fails and time.monotonic() - t0 < 5:
        _pump(qapp, 50)
    assert len(fails) == 1 and fails[0][0] == "NOT_A_FILE"

    assert not page.is_importing()
    page.close()


def test_crashes_page_delete_confirmation(qapp, tmp_path: Path):
    from mmis.ui.pages.crashes import CrashesPage

    service = create_profile_service(tmp_path / "data")
    rec = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Delete test", profile_name=None, source_name="del.log")

    page = CrashesPage(service)
    page.set_scope("all")
    page.refresh()
    page.resize(800, 600)
    page.show()
    _pump(qapp, 50)

    assert rec.id in page.visible_crash_ids()

    # Cancel delete
    asked = []
    page.confirm_delete = lambda item: (asked.append(item), False)[1]
    page.request_delete(rec.id)
    _pump(qapp, 50)
    assert len(asked) == 1
    assert rec.id in page.visible_crash_ids()

    # Confirm delete
    page.confirm_delete = lambda item: True
    page.request_delete(rec.id)
    _pump(qapp, 50)
    assert rec.id not in page.visible_crash_ids()
    assert len(service.list_crashes()) == 0

    page.close()


def test_crashes_page_scope_all_profile_indication(qapp, tmp_path: Path):
    from mmis.ui.pages.crashes import CrashesPage
    from mmis.ui import strings

    service = create_profile_service(tmp_path / "data")
    p1 = service.add_profile("Alpha Pack", tmp_path / "alpha")
    p2 = service.add_profile("Beta Pack", tmp_path / "beta")

    c1 = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Alpha err", profile_name=p1.name, source_name="alpha.log")
    c2 = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Beta err", profile_name=p2.name, source_name="beta.log")
    c3 = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Unlinked err", profile_name=None, source_name="unlinked.log")

    page = CrashesPage(service)
    page.set_active_profile(p1.name)
    page.set_scope("all")
    page.resize(1000, 700)
    page.show()
    _pump(qapp, 100)

    all_ids = page.visible_crash_ids()
    assert c1.id in all_ids
    assert c2.id in all_ids
    assert c3.id in all_ids

    # Check that in scope All, the list cards show Beta Pack and Unlinked label
    from PySide6.QtWidgets import QLabel
    page_texts = [lbl.text() for lbl in page.findChildren(QLabel) if lbl.isVisible()]
    page_text_blob = " ".join(page_texts)

    assert "Beta Pack" in page_text_blob
    assert strings.CRASH_UNLINKED_LABEL in page_text_blob

    # Select unlinked crash -> overview shows "Profile: Unlinked"
    page.select_crash(c3.id)
    _pump(qapp, 50)
    detail_texts = " ".join([lbl.text() for lbl in page.detail_panel.findChildren(QLabel) if lbl.isVisible()])
    assert f"Profile: {strings.CRASH_UNLINKED_LABEL}" in detail_texts

    # Select Beta Pack crash -> overview shows "Profile: Beta Pack"
    page.select_crash(c2.id)
    _pump(qapp, 50)
    detail_texts_beta = " ".join([lbl.text() for lbl in page.detail_panel.findChildren(QLabel) if lbl.isVisible()])
    assert "Profile: Beta Pack" in detail_texts_beta

    page.close()

