"""Unit tests for mmis.services.crash_view module."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mmis.services import crash_view
from mmis.services.profiles import create_profile_service
from mmis.storage.repository import ProfileRepository

FIXTURES_CRASH = Path(__file__).resolve().parent / "fixtures" / "crash"


def test_sha256_file(tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"hello world of crash logs 12345")
    expected = hashlib.sha256(b"hello world of crash logs 12345").hexdigest()
    assert crash_view.sha256_file(sample) == expected


def test_list_crashes_and_scopes(tmp_path: Path):
    data_dir = tmp_path / "data"
    service = create_profile_service(data_dir)

    p1_dir = tmp_path / "p1"
    p1_dir.mkdir()
    p1 = service.add_profile("Profile Alpha", p1_dir)

    p2_dir = tmp_path / "p2"
    p2_dir.mkdir()
    p2 = service.add_profile("Profile Beta", p2_dir)

    c1 = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Alpha error\n\tat com.mod.Main.init(Main.java:10)", profile_name=p1.name, source_name="alpha.log")
    c2 = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Beta error\n\tat com.mod.Beta.init(Beta.java:20)", profile_name=p2.name, source_name="beta.log")
    c_unlinked = service.import_crash(b"Exception in thread \"main\" java.lang.RuntimeException: Unlinked error\n\tat com.mod.Unl.init(Unl.java:30)", profile_name=None, source_name="unlinked.log")

    # Scope All
    all_items = crash_view.list_crashes(service, None)
    all_ids = [it.id for it in all_items]
    assert c1.id in all_ids
    assert c2.id in all_ids
    assert c_unlinked.id in all_ids

    unl_item = next(it for it in all_items if it.id == c_unlinked.id)
    assert unl_item.profile_name is None
    p1_item = next(it for it in all_items if it.id == c1.id)
    assert p1_item.profile_name == p1.name

    # Scope Profile Alpha
    alpha_items = crash_view.list_crashes(service, p1.name)
    alpha_ids = [it.id for it in alpha_items]
    assert c1.id in alpha_ids
    assert c2.id not in alpha_ids
    assert c_unlinked.id not in alpha_ids


def test_get_crash_detail(tmp_path: Path):
    data_dir = tmp_path / "data"
    service = create_profile_service(data_dir)

    fixture_path = FIXTURES_CRASH / "fabric_crash.txt"
    if fixture_path.exists():
        raw_bytes = fixture_path.read_bytes()
    else:
        raw_bytes = b"Exception in thread \"main\" java.lang.RuntimeException: Sample error\n\tat com.mod.Test.init(Test.java:10)"

    rec = service.import_crash(raw_bytes, profile_name=None, source_name="fabric_crash.txt")
    detail = crash_view.get_crash_detail(service, rec.id)

    assert detail.item.id == rec.id
    assert detail.item.source_name == "fabric_crash.txt"
    assert detail.item.profile_name is None
    assert isinstance(detail.suspects, list)
    assert isinstance(detail.findings, list)

    for s in detail.suspects:
        assert s.certainty in ("Certain", "Possible", "Insufficient data")
        assert isinstance(s.evidence_lines, list)

    for f in detail.findings:
        assert f.certainty in ("Certain", "Possible", "Insufficient data")


def test_find_duplicate(tmp_path: Path):
    data_dir = tmp_path / "data"
    service = create_profile_service(data_dir)

    p_dir = tmp_path / "pack"
    p_dir.mkdir()
    prof = service.add_profile("Test Pack", p_dir)

    content = b"Exception in thread \"main\" java.lang.RuntimeException: Duplicate test"
    rec = service.import_crash(content, profile_name=prof.name, source_name="dup.log")

    # Match same sha and same profile
    dup_id = crash_view.find_duplicate(service, rec.sha256, prof.name)
    assert dup_id == rec.id

    # Non-matching profile
    assert crash_view.find_duplicate(service, rec.sha256, "NonExistent") is None
    assert crash_view.find_duplicate(service, rec.sha256, None) is None

    # Unlinked duplicate
    rec_unl = service.import_crash(content + b" unlinked", profile_name=None, source_name="unl.log")
    dup_unl = crash_view.find_duplicate(service, rec_unl.sha256, None)
    assert dup_unl == rec_unl.id
    assert crash_view.find_duplicate(service, rec_unl.sha256, prof.name) is None


def test_format_imported_at():
    assert crash_view.format_imported_at("") == ""
    assert crash_view.format_imported_at("2026-10-06T12:00:00Z") == "2026-10-06 12:00 UTC"
    assert crash_view.format_imported_at("2026-10-06T14:00:00+02:00") == "2026-10-06 12:00 UTC"
    assert crash_view.format_imported_at("2026-10-06T08:30:00") == "2026-10-06 08:30 UTC"
    # Unparseable fallback
    assert crash_view.format_imported_at("invalid-timestamp") == "invalid-timestamp"


def test_format_crash_kind():
    from mmis.ui import strings

    assert crash_view.format_crash_kind("") == ""
    assert crash_view.format_crash_kind("crash_report") == strings.CRASH_KIND_CRASH_REPORT
    assert crash_view.format_crash_kind("CRASH_REPORT") == strings.CRASH_KIND_CRASH_REPORT
    assert crash_view.format_crash_kind("log_file") == strings.CRASH_KIND_LOG_FILE
    assert crash_view.format_crash_kind("hs_err") == strings.CRASH_KIND_HS_ERR
    assert crash_view.format_crash_kind("unknown") == strings.CRASH_KIND_UNKNOWN
    # Unmapped fallback
    assert crash_view.format_crash_kind("custom_kind_x") == "custom_kind_x"


def test_crash_list_item_display_properties():
    from mmis.ui import strings

    item_linked = crash_view.CrashListItem(
        id=1,
        source_name="test.txt",
        imported_at="2026-10-06T12:00:00Z",
        kind="crash_report",
        mc_version="1.20.1",
        loader="Forge",
        top_suspect="some_mod",
        error_count=1,
        warning_count=0,
        info_count=0,
        file_size=1024,
        sha256="abc",
        profile_name="Demo Pack",
    )
    assert item_linked.display_imported_at == "2026-10-06 12:00 UTC"
    assert item_linked.display_kind == strings.CRASH_KIND_CRASH_REPORT
    assert item_linked.display_profile_name == "Demo Pack"

    item_unlinked = crash_view.CrashListItem(
        id=2,
        source_name="unl.txt",
        imported_at="2026-10-06T12:00:00Z",
        kind="unmapped_kind",
        mc_version=None,
        loader=None,
        top_suspect=None,
        error_count=0,
        warning_count=0,
        info_count=0,
        file_size=512,
        sha256="def",
        profile_name=None,
    )
    assert item_unlinked.display_kind == "unmapped_kind"
    assert item_unlinked.display_profile_name == strings.CRASH_UNLINKED_LABEL

