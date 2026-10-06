"""Tests for the analyzer extension point (engine.ANALYZERS contract)."""

from __future__ import annotations

from mmis.analysis import engine
from mmis.analysis.conflicts import analyze_conflicts
from mmis.analysis.dependencies import analyze_dependencies
from mmis.domain.findings import CODE_TO_CATEGORY, CONFLICT_DUPLICATE_MOD
from mmis.domain.models import (
    Certainty,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)


def _make_dummy_scan() -> ScanResult:
    return ScanResult(
        scanned_path="dummy_pack",
        mods_dir="dummy_pack/mods",
        detected_loader=Loader.FABRIC,
        detected_mc_version="1.20.1",
        jars=[
            JarFile(
                path="mods/test.jar",
                size_bytes=1024,
                sha256="abc",
                mods=["testmod"],
            )
        ],
        mods=[
            ModInfo(
                id="testmod",
                name="Test Mod",
                version="1.0.0",
                loaders=[Loader.FABRIC],
                jar_path="mods/test.jar",
            )
        ],
    )


def test_custom_analyzer_extension(monkeypatch):
    """Register custom analyzer via engine.ANALYZERS and verify findings aggregation."""
    # Ensure isolation by copying ANALYZERS list via monkeypatch
    monkeypatch.setattr(engine, "ANALYZERS", list(engine.ANALYZERS))

    custom_code = "CUSTOM_UNKNOWN_CODE_XYZ"
    assert custom_code not in CODE_TO_CATEGORY

    custom_finding_1 = Finding(
        code=custom_code,
        message="Custom finding from extension analyzer",
        severity=Severity.ERROR,
        certainty=Certainty.CERTAIN,
        evidence=[Evidence(file="mods/test.jar", field="custom_field", raw_value="val")],
        suggestion="Fix custom item",
    )
    custom_finding_2 = Finding(
        code=CONFLICT_DUPLICATE_MOD,
        message="Known code from extension",
        severity=Severity.INFO,
        certainty=Certainty.POSSIBLE,
        evidence=[Evidence(file="mods/test.jar")],
    )

    def fake_analyzer(scan: ScanResult) -> list[Finding]:
        return [custom_finding_1, custom_finding_2]

    engine.ANALYZERS.append(fake_analyzer)

    scan = _make_dummy_scan()
    report = engine.analyze_pack(scan)

    # 1. Findings appear in report.findings
    finding_codes = [f.code for f in report.findings]
    assert custom_code in finding_codes
    assert CONFLICT_DUPLICATE_MOD in finding_codes

    # 2. Severity counts include custom findings
    assert report.severity_counts[Severity.ERROR.value] >= 1
    assert report.severity_counts[Severity.INFO.value] >= 1

    # 3. Certainty counts include custom findings
    assert report.certainty_counts[Certainty.CERTAIN.value] >= 1
    assert report.certainty_counts[Certainty.POSSIBLE.value] >= 1

    # 4. Unknown finding code falls into the "metadata" default category
    assert report.category_counts.get("metadata", 0) >= 1

    # 5. Built-in analyzers still executed
    assert analyze_dependencies in engine.ANALYZERS
    assert analyze_conflicts in engine.ANALYZERS


def test_analyzer_findings_output_ordering_is_deterministic(monkeypatch):
    """Output ordering of findings is deterministic regardless of the order analyzers return findings."""
    f_alpha = Finding(
        code="ALPHA_CODE",
        message="Zeta message",
        severity=Severity.WARNING,
        certainty=Certainty.CERTAIN,
        evidence=[Evidence(file="b.jar")],
    )
    f_beta = Finding(
        code="ALPHA_CODE",
        message="Beta message",
        severity=Severity.ERROR,
        certainty=Certainty.POSSIBLE,
        evidence=[Evidence(file="a.jar")],
    )
    f_gamma = Finding(
        code="GAMMA_CODE",
        message="Gamma message",
        severity=Severity.INFO,
        certainty=Certainty.INSUFFICIENT_DATA,
        evidence=[Evidence(file="c.jar")],
    )

    # Run 1: returned in order [f_gamma, f_alpha, f_beta]
    monkeypatch.setattr(engine, "ANALYZERS", [lambda s: [f_gamma, f_alpha, f_beta]])
    scan = _make_dummy_scan()
    report_1 = engine.analyze_pack(scan)

    # Run 2: returned in reverse order [f_beta, f_gamma, f_alpha]
    monkeypatch.setattr(engine, "ANALYZERS", [lambda s: [f_beta, f_gamma, f_alpha]])
    report_2 = engine.analyze_pack(scan)

    # Both reports must have findings in identical order
    assert [f.to_dict() for f in report_1.findings] == [f.to_dict() for f in report_2.findings]


def test_analyzer_list_is_clean_after_monkeypatch():
    """Verify built-in ANALYZERS list is preserved across test runs."""
    assert engine.ANALYZERS == [analyze_dependencies, analyze_conflicts]
