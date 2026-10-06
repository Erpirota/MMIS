"""Tests for diff engine covering aggregate codes stability and analysis findings inclusion."""

import pytest
from mmis.analysis.diff import diff_scans
from mmis.domain.findings import (
    AMBIGUOUS_LOADER,
    CONFLICT_DUPLICATE_MOD,
    DEP_MISSING_REQUIRED,
    PLATFORM_DEP_UNVERIFIED,
)
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
from mmis.domain.versions import VersionRange


def test_diff_includes_analysis_findings():
    """Verify that analysis findings (missing dep, duplicate mods) appear in ScanDiff."""
    # Scan A: mod_a has a satisfied dep on mod_b
    m_a = ModInfo(id="mod_a", version="1.0.0", jar_path="mods/a.jar", dependencies=[Dependency(mod_id="mod_b")])
    m_b = ModInfo(id="mod_b", version="1.0.0", jar_path="mods/b.jar")
    jar_a = JarFile(path="mods/a.jar", size_bytes=100, sha256="1", mods=["mod_a"])
    jar_b = JarFile(path="mods/b.jar", size_bytes=100, sha256="2", mods=["mod_b"])
    scan_a = ScanResult(scanned_path=".", mods_dir="mods", jars=[jar_a, jar_b], mods=[m_a, m_b])

    # Scan B: mod_b was removed -> mod_a now has a missing required dependency!
    scan_b = ScanResult(scanned_path=".", mods_dir="mods", jars=[jar_a], mods=[m_a])

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes

    added_codes = [f.code for f in diff.findings_added]
    assert DEP_MISSING_REQUIRED in added_codes


def test_aggregate_codes_not_resolved_plus_added_when_evidence_changes():
    """Aggregate codes (e.g. PLATFORM_DEP_UNVERIFIED, AMBIGUOUS_LOADER) identify by code only.

    When evidence list changes between scans, it must NOT be reported as both resolved and added.
    """
    ev_a1 = Evidence(file="mods/a.jar", message="Platform dep 1")
    finding_a = Finding(
        code=PLATFORM_DEP_UNVERIFIED,
        severity=Severity.INFO,
        certainty=Certainty.INSUFFICIENT_DATA,
        message="Platform dep unverified",
        evidence=[ev_a1],
        related_mod_ids=["a"],
    )
    scan_a = ScanResult(scanned_path=".", mods_dir="mods", findings=[finding_a])

    # Scan B has a second jar in evidence
    ev_b1 = Evidence(file="mods/a.jar", message="Platform dep 1")
    ev_b2 = Evidence(file="mods/b.jar", message="Platform dep 2")
    finding_b = Finding(
        code=PLATFORM_DEP_UNVERIFIED,
        severity=Severity.INFO,
        certainty=Certainty.INSUFFICIENT_DATA,
        message="Platform dep unverified",
        evidence=[ev_b1, ev_b2],
        related_mod_ids=["a", "b"],
    )
    scan_b = ScanResult(scanned_path=".", mods_dir="mods", findings=[finding_b])

    diff = diff_scans(scan_a, scan_b)
    # Must NOT report PLATFORM_DEP_UNVERIFIED as added or resolved
    added_codes = [f.code for f in diff.findings_added]
    resolved_codes = [f.code for f in diff.findings_resolved]

    assert PLATFORM_DEP_UNVERIFIED not in added_codes
    assert PLATFORM_DEP_UNVERIFIED not in resolved_codes
