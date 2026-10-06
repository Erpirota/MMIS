"""Unit tests for pure functional issues_view service (zero Qt)."""

from __future__ import annotations

from mmis.analysis.engine import AnalysisReport
from mmis.domain.models import Certainty, Evidence, Finding, JarFile, ModInfo, ScanResult, Severity
from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.issues_view import (
    IssueEvidence,
    IssueFilterCriteria,
    IssueItem,
    build_issues_view,
    filter_issues,
    group_issues,
)


def _make_dummy_record() -> ScanRecord:
    summary = ScanSummary(
        id=1,
        profile_id=1,
        scanned_at="2026-10-05T12:00:00Z",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=2,
        mod_count=2,
        error_count=1,
        warning_count=1,
        info_count=1,
    )
    f_err = Finding(
        code="DEP_MISSING_REQUIRED",
        message="Mod alpha is missing library beta",
        severity=Severity.ERROR,
        certainty=Certainty.CERTAIN,
        related_mod_ids=["alpha"],
        evidence=[Evidence(file="alpha.jar", field="depends", raw_value="beta@>=1.0")],
        suggestion="Install beta library",
    )
    f_warn = Finding(
        code="CONFLICT_MC_VERSION_MISMATCH",
        message="Mod gamma requires MC 1.21",
        severity=Severity.WARNING,
        certainty=Certainty.POSSIBLE,
        related_mod_ids=["gamma"],
        evidence=[Evidence(file="gamma.jar", field="minecraft", raw_value=">=1.21")],
        suggestion="Update Minecraft version",
    )
    f_info = Finding(
        code="PACK_MANIFEST_DETECTED",
        message="Detected CurseForge pack manifest",
        severity=Severity.INFO,
        certainty=Certainty.INSUFFICIENT_DATA,
        related_mod_ids=[],
        evidence=[Evidence(file="manifest.json")],
    )

    res = ScanResult(
        scanned_path="/dummy",
        mods_dir="/dummy/mods",
        jars=[],
        mods=[],
        findings=[f_err, f_info],
    )
    analysis = AnalysisReport(
        findings=[f_warn],
        severity_counts={"error": 1, "warning": 1, "info": 1},
    )
    return ScanRecord(summary=summary, result=res, analysis=analysis)


def test_build_issues_view_deterministic_ordering():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    assert len(items) == 3
    # Order must be Error > Warning > Info
    assert items[0].severity == "error"
    assert items[0].code == "DEP_MISSING_REQUIRED"
    assert items[0].certainty == "Certain"
    assert items[0].is_pack_level is False
    assert items[0].related_mods == ["alpha"]
    assert len(items[0].evidence) == 1
    assert items[0].evidence[0].raw_value == "beta@>=1.0"

    assert items[1].severity == "warning"
    assert items[1].code == "CONFLICT_MC_VERSION_MISMATCH"
    assert items[1].certainty == "Possible"
    assert items[1].is_pack_level is False

    assert items[2].severity == "info"
    assert items[2].code == "PACK_MANIFEST_DETECTED"
    assert items[2].certainty == "Insufficient data"
    assert items[2].is_pack_level is True


def test_filter_issues_by_severity():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    criteria = IssueFilterCriteria(severities={"error"})
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 1
    assert filtered[0].severity == "error"


def test_filter_issues_by_certainty():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    criteria = IssueFilterCriteria(certainties={"Possible"})
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 1
    assert filtered[0].certainty == "Possible"


def test_filter_issues_by_search_query():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    # Search by mod ID
    criteria = IssueFilterCriteria(search_text="gamma")
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 1
    assert filtered[0].related_mods == ["gamma"]

    # Search by code
    criteria = IssueFilterCriteria(search_text="missing")
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 1
    assert filtered[0].code == "DEP_MISSING_REQUIRED"

    # Search unmatched
    criteria = IssueFilterCriteria(search_text="nonexistent")
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 0


def test_filter_issues_pack_level_only():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    criteria = IssueFilterCriteria(pack_level_only=True)
    filtered = filter_issues(items, criteria)
    assert len(filtered) == 1
    assert filtered[0].is_pack_level is True


def test_group_issues():
    rec = _make_dummy_record()
    items = build_issues_view(rec)

    # Group by category
    by_cat = group_issues(items, "category")
    assert "dependency" in by_cat
    assert "metadata" in by_cat

    # Group by severity
    by_sev = group_issues(items, "severity")
    assert "error" in by_sev
    assert "warning" in by_sev
    assert "info" in by_sev

    # Group by mod
    by_mod = group_issues(items, "mod")
    assert "Pack-level" in by_mod
    assert "alpha" in by_mod
    assert "gamma" in by_mod
