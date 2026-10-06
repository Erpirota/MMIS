"""Unit and integration tests for diagnostic report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mmis.domain.crash import CrashAnalysisResult, CrashRecord, CrashReportKind, CrashSuspect, ExceptionInfo
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
from mmis.domain.profiles import ProfileSummary, ScanRecord, ScanSummary
from mmis.analysis.engine import AnalysisReport
from mmis.services.export import export_report_json
from mmis.services.report import DiagnosticReport, build_diagnostic_report


def _create_sample_scan_record(tmp_path: Path) -> ScanRecord:
    summary = ScanSummary(
        id=1,
        profile_id=1,
        scanned_at="2026-10-05T12:00:00+00:00",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=2,
        mod_count=2,
        error_count=1,
        warning_count=1,
        info_count=0,
        label="Test Scan",
        pinned=False,
    )
    mods = [
        ModInfo(
            id="mod_a",
            name="Mod A",
            version="1.0.0",
            loaders=[Loader.FABRIC],
            jar_path=str(tmp_path / "mods" / "mod_a.jar"),
            description="First mod",
        ),
        ModInfo(
            id="mod_b",
            name="Mod B",
            version="2.0.0",
            loaders=[Loader.FABRIC],
            jar_path=str(tmp_path / "mods" / "mod_b.jar"),
            description="Second mod",
        ),
    ]
    jars = [
        JarFile(path=str(tmp_path / "mods" / "mod_a.jar"), size_bytes=1024, sha256="aaa"),
        JarFile(path=str(tmp_path / "mods" / "mod_b.jar"), size_bytes=2048, sha256="bbb"),
    ]
    scan_res = ScanResult(
        scanned_path=str(tmp_path),
        mods_dir=str(tmp_path / "mods"),
        jars=jars,
        mods=mods,
        findings=[
            Finding(
                code="CORRUPT_JAR",
                message="Jar file is corrupted",
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                related_mod_ids=["mod_a"],
                evidence=[Evidence(file="mods/mod_a.jar", message="Invalid ZIP CRC")],
                suggestion="Re-download or remove mod_a.jar",
            )
        ],
        detected_loader=Loader.FABRIC,
        detected_mc_version="1.20.1",
    )
    analysis_rep = AnalysisReport(
        findings=[
            Finding(
                code="CONFLICT_LOADER_MISMATCH",
                message="Mod expects Forge but pack is Fabric",
                severity=Severity.WARNING,
                certainty=Certainty.POSSIBLE,
                related_mod_ids=["mod_b"],
                evidence=[Evidence(file="mods/mod_b.jar", field="loader", raw_value="forge")],
                suggestion="Replace mod_b with a Fabric version",
            )
        ],
        severity_counts={"error": 1, "warning": 1, "info": 0},
        certainty_counts={"certain": 1, "possible": 1, "insufficient_data": 0},
        category_counts={"metadata": 1, "conflict": 1},
    )
    return ScanRecord(summary=summary, result=scan_res, analysis=analysis_rep)


def test_build_diagnostic_report_counts_and_content(tmp_path: Path):
    rec = _create_sample_scan_record(tmp_path)
    report = build_diagnostic_report(
        profile_name="DemoPack",
        scan_record=rec,
        profile_path=str(tmp_path),
    )

    assert report.schema_version == 1
    assert report.profile_name == "DemoPack"
    assert report.status == "Errors"
    assert report.severity_counts == {"error": 1, "warning": 1, "info": 0}
    assert report.certainty_counts["Certain"] == 1
    assert report.certainty_counts["Possible"] == 1
    assert report.certainty_counts["Insufficient data"] == 0

    # Recommendations: ordered by severity (error > warning)
    assert len(report.recommendations) == 2
    assert report.recommendations[0].severity == "error"
    assert "Re-download or remove mod_a.jar" in report.recommendations[0].suggestion
    assert report.recommendations[1].severity == "warning"
    assert "Replace mod_b" in report.recommendations[1].suggestion

    # Mod inventory
    assert report.mod_inventory.total_mods == 2
    assert report.mod_inventory.enabled_mods == 2
    assert report.mod_inventory.disabled_mods == 0


def test_build_diagnostic_report_with_crash_analysis(tmp_path: Path):
    rec = _create_sample_scan_record(tmp_path)
    c_rec = CrashRecord(
        id=42,
        profile_id=1,
        imported_at="2026-10-05T12:30:00+00:00",
        source_name="crash-2026-10-05.txt",
        file_size=5000,
        sha256="c1234",
        kind="crash_report",
        mc_version="1.20.1",
        loader="fabric",
        top_suspect="mod_a",
        error_count=1,
        warning_count=0,
        info_count=0,
    )
    c_analysis = CrashAnalysisResult(
        kind=CrashReportKind.CRASH_REPORT,
        mc_version="1.20.1",
        detected_loader="fabric",
        root_cause=ExceptionInfo(exception_class="java.lang.NullPointerException", message="Mod A failed to initialize"),
        suspects=[
            CrashSuspect(
                mod_id="mod_a",
                name="Mod A",
                reason="Explicit error during initialization",
                certainty=Certainty.CERTAIN,
                rank=1,
                attribution="loader_statement",
            )
        ],
        findings=[
            Finding(
                code="CRASH_CLASS_NOT_FOUND",
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message="Class not found during startup",
                evidence=[Evidence(file="crash.txt", message="NoClassDefFoundError")],
                related_mod_ids=["mod_a"],
                suggestion="Update Mod A dependencies",
            )
        ],
    )

    report = build_diagnostic_report(
        profile_name="DemoPack",
        scan_record=rec,
        profile_path=str(tmp_path),
        latest_crash=(c_rec, c_analysis),
    )

    assert report.latest_crash is not None
    assert report.latest_crash.crash_id == 42
    assert report.latest_crash.top_suspect == "mod_a"
    assert "NullPointerException" in (report.latest_crash.root_cause or "")
    assert len(report.latest_crash.suspects) == 1
    assert report.latest_crash.suspects[0]["certainty"] == "Certain"


def test_report_json_determinism_and_no_absolute_paths(tmp_path: Path):
    rec = _create_sample_scan_record(tmp_path)
    report1 = build_diagnostic_report("DemoPack", rec, profile_path=str(tmp_path))
    json1 = export_report_json(report1)

    report2 = build_diagnostic_report("DemoPack", rec, profile_path=str(tmp_path))
    json2 = export_report_json(report2)

    assert json1 == json2

    parsed = json.loads(json1)
    assert parsed["profile_path"] == str(tmp_path)

    # Walk all other string values in the parsed report; ensure no other absolute paths appear
    def check_no_abs(obj, key_name=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k != "profile_path":
                    check_no_abs(v, k)
        elif isinstance(obj, list):
            for item in obj:
                check_no_abs(item, key_name)
        elif isinstance(obj, str):
            # Check for drive letter path like C:\ or Unix /home /tmp
            if (len(obj) > 3 and obj[1:3] == ":\\") or (obj.startswith("/") and " " not in obj):
                assert False, f"Forbidden absolute path found in field '{key_name}': {obj}"

    check_no_abs(parsed)
