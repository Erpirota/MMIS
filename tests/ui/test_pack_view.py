"""Unit tests for pure pack_view presentation model and filtering logic (A1)."""

from __future__ import annotations

from mmis.analysis.engine import AnalysisReport
from mmis.domain.models import (
    Certainty,
    Dependency,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)
from mmis.services.pack_view import FilterCriteria, ModRow, build_pack_view, filter_and_sort


def _make_dummy_scan_result() -> ScanResult:
    jars = [
        JarFile(path="mods/fabric_mod.jar", size_bytes=1000, sha256="abc1"),
        JarFile(path="mods/dual_mod.jar", size_bytes=2000, sha256="abc2"),
        JarFile(path="mods/disabled_mod.jar.disabled", size_bytes=3000, sha256="abc3", is_disabled=True),
        JarFile(path="mods/nested_parent.jar", size_bytes=4000, sha256="abc4"),
    ]

    mods = [
        ModInfo(
            id="mod_a",
            name="Alpha Mod",
            version="1.0.0",
            loaders=[Loader.FABRIC],
            jar_path="mods/fabric_mod.jar",
            description="Alpha description",
            authors=["Alice"],
        ),
        # Dual loader records for same jar and mod ID
        ModInfo(
            id="dual_mod",
            name="Dual Mod Forge",
            version="2.0.0",
            loaders=[Loader.FORGE],
            jar_path="mods/dual_mod.jar",
            metadata_file="META-INF/mods.toml",
        ),
        ModInfo(
            id="dual_mod",
            name="Dual Mod Fabric",
            version="2.0.0",
            loaders=[Loader.FABRIC],
            jar_path="mods/dual_mod.jar",
            metadata_file="fabric.mod.json",
        ),
        # Disabled jar mod
        ModInfo(
            id="mod_disabled",
            name="Disabled Mod",
            version="0.5.0",
            loaders=[Loader.FABRIC],
            jar_path="mods/disabled_mod.jar.disabled",
        ),
        # Nested mod
        ModInfo(
            id="mod_nested",
            name=None,  # Missing name
            version=None,  # Missing version
            loaders=[],  # Missing loaders
            jar_path="mods/nested_parent.jar",
            is_nested=True,
        ),
    ]

    findings = [
        Finding(
            code="MOD_ERROR",
            severity=Severity.ERROR,
            certainty=Certainty.CERTAIN,
            message="Critical problem",
            evidence=[Evidence(file="mods/fabric_mod.jar")],
            related_mod_ids=["mod_a"],
        ),
        Finding(
            code="MOD_WARN",
            severity=Severity.WARNING,
            certainty=Certainty.POSSIBLE,
            message="Minor warning",
            evidence=[Evidence(file="mods/nested_parent.jar")],
            related_mod_ids=["mod_nested"],
        ),
    ]

    return ScanResult(
        scanned_path="/dummy/pack",
        mods_dir="/dummy/pack/mods",
        jars=jars,
        mods=mods,
        findings=findings,
        detected_loader=Loader.FABRIC,
    )


def test_build_pack_view_consolidation_and_attributes():
    scan = _make_dummy_scan_result()
    rows = build_pack_view(scan)

    # 4 distinct mods expected: mod_a, dual_mod, mod_disabled, mod_nested
    assert len(rows) == 4

    row_map = {r.mod_id: r for r in rows}

    # mod_a check: has error finding -> status error
    a = row_map["mod_a"]
    assert a.name == "Alpha Mod"
    assert a.status == "error"
    assert a.error_count == 1
    assert a.issue_count == 1
    assert not a.is_disabled
    assert not a.is_nested

    # dual_mod check: pack loader is Fabric, so Fabric record should be chosen
    dual = row_map["dual_mod"]
    assert dual.mod_info.metadata_file == "fabric.mod.json"
    assert len(dual.other_formats) == 1
    assert dual.other_formats[0].metadata_file == "META-INF/mods.toml"
    assert dual.status == "ok"
    assert dual.issue_count == 0

    # disabled jar check
    dis = row_map["mod_disabled"]
    assert dis.is_disabled
    assert dis.status == "disabled"

    # nested mod check with missing metadata
    nest = row_map["mod_nested"]
    assert nest.is_nested
    assert nest.name == "mod_nested"  # Falls back to ID
    assert nest.version == "Unknown"
    assert nest.loader == "Unknown"
    assert nest.status == "warning"
    assert nest.warning_count == 1


def test_filter_and_sort_search():
    scan = _make_dummy_scan_result()
    rows = build_pack_view(scan)

    # Search by ID
    res = filter_and_sort(rows, FilterCriteria(search_text="dual"))
    assert len(res) == 1
    assert res[0].mod_id == "dual_mod"

    # Case-insensitive search by name
    res = filter_and_sort(rows, FilterCriteria(search_text="ALPHA"))
    assert len(res) == 1
    assert res[0].mod_id == "mod_a"

    # Search by jar name
    res = filter_and_sort(rows, FilterCriteria(search_text="nested_parent"))
    assert len(res) == 1
    assert res[0].mod_id == "mod_nested"


def test_filter_and_sort_criteria():
    scan = _make_dummy_scan_result()
    rows = build_pack_view(scan)

    # Status filter
    errs = filter_and_sort(rows, FilterCriteria(status="error"))
    assert len(errs) == 1
    assert errs[0].mod_id == "mod_a"

    dis = filter_and_sort(rows, FilterCriteria(status="disabled"))
    assert len(dis) == 1
    assert dis[0].mod_id == "mod_disabled"

    # Has issues filter
    issues = filter_and_sort(rows, FilterCriteria(has_issues=True))
    assert len(issues) == 2  # mod_a (error) and mod_nested (warning)

    clean = filter_and_sort(rows, FilterCriteria(has_issues=False))
    assert len(clean) == 2  # dual_mod and mod_disabled

    # Sorting
    sorted_desc = filter_and_sort(rows, FilterCriteria(sort_key="name", sort_ascending=False))
    assert sorted_desc[0].name >= sorted_desc[-1].name

    sorted_issues = filter_and_sort(rows, FilterCriteria(sort_key="issue_count", sort_ascending=False))
    assert sorted_issues[0].issue_count >= sorted_issues[-1].issue_count


def test_pack_view_aggregate_codes_and_info_severity():
    from mmis.domain.findings import AGGREGATE_CODES, AMBIGUOUS_LOADER, PLATFORM_DEP_UNVERIFIED
    from mmis.services.pack_view import calculate_pack_health

    jars = [
        JarFile(path="mods/mod_clean.jar", size_bytes=1000, sha256="111"),
        JarFile(path="mods/mod_info_only.jar", size_bytes=1000, sha256="222"),
        JarFile(path="mods/mod_warn.jar", size_bytes=1000, sha256="333"),
        JarFile(path="mods/mod_err.jar", size_bytes=1000, sha256="444"),
    ]
    mods = [
        ModInfo(id="mod_clean", name="Clean Mod", version="1.0.0", jar_path="mods/mod_clean.jar"),
        ModInfo(id="mod_info_only", name="Info Only Mod", version="1.0.0", jar_path="mods/mod_info_only.jar"),
        ModInfo(id="mod_warn", name="Warn Mod", version="1.0.0", jar_path="mods/mod_warn.jar"),
        ModInfo(id="mod_err", name="Err Mod", version="1.0.0", jar_path="mods/mod_err.jar"),
    ]
    findings = [
        # Pack-level aggregate findings: must NOT be attached to mods or affect mod status
        Finding(code=AMBIGUOUS_LOADER, severity=Severity.WARNING, certainty=Certainty.CERTAIN, message="Ambiguous loader", evidence=[Evidence(file="pack")], related_mod_ids=["mod_clean", "mod_info_only"]),
        Finding(code=PLATFORM_DEP_UNVERIFIED, severity=Severity.INFO, certainty=Certainty.CERTAIN, message="Platform dep info", evidence=[Evidence(file="pack")], related_mod_ids=["mod_clean"]),
        # Mod-specific info finding: must NOT raise status above ok
        Finding(code="MOD_INFO_HINT", severity=Severity.INFO, certainty=Certainty.CERTAIN, message="Helpful info", evidence=[Evidence(file="mods/mod_info_only.jar")], related_mod_ids=["mod_info_only"]),
        # Mod warning and error
        Finding(code="MOD_SPECIFIC_WARN", severity=Severity.WARNING, certainty=Certainty.CERTAIN, message="Warning on mod", evidence=[Evidence(file="mods/mod_warn.jar")], related_mod_ids=["mod_warn"]),
        Finding(code="MOD_SPECIFIC_ERR", severity=Severity.ERROR, certainty=Certainty.CERTAIN, message="Error on mod", evidence=[Evidence(file="mods/mod_err.jar")], related_mod_ids=["mod_err"]),
    ]
    scan = ScanResult(
        scanned_path="/dummy",
        mods_dir="/dummy/mods",
        jars=jars,
        mods=mods,
        findings=findings,
    )

    rows = build_pack_view(scan)
    row_map = {r.mod_id: r for r in rows}

    # Clean mod: aggregate finding AMBIGUOUS_LOADER excluded -> status ok, 0 findings
    assert row_map["mod_clean"].status == "ok"
    assert len(row_map["mod_clean"].related_findings) == 0
    assert row_map["mod_clean"].issue_count == 0

    # Info-only mod: MOD_INFO_HINT attached, but status stays "ok", issue_count 0
    assert row_map["mod_info_only"].status == "ok"
    assert len(row_map["mod_info_only"].related_findings) == 1
    assert row_map["mod_info_only"].issue_count == 0

    # Warn mod: status warning, issue_count 1
    assert row_map["mod_warn"].status == "warning"
    assert row_map["mod_warn"].warning_count == 1
    assert row_map["mod_warn"].issue_count == 1

    # Err mod: status error, issue_count 1
    assert row_map["mod_err"].status == "error"
    assert row_map["mod_err"].error_count == 1
    assert row_map["mod_err"].issue_count == 1

    # Pack health calculation:
    health = calculate_pack_health(rows, scan)
    assert health.total_active_mods == 4
    assert health.clean_mod_count == 2
    assert health.percentage == 50
    assert health.pack_status == "Errors"
    assert "2 of 4 mods without issues" in health.label
