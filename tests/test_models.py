"""Tests for domain model serialization round-trip and validation."""

import pytest
from mmis.domain.models import (
    Certainty,
    Dependency,
    DependencyKind,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)
from mmis.domain.versions import VersionRange


def test_scan_result_to_dict_and_from_dict_roundtrip():
    vr = VersionRange.parse(">=1.20 <=1.20.4", syntax="fabric")
    dep = Dependency(
        mod_id="fabricloader",
        version_range=vr,
        kind=DependencyKind.REQUIRED,
        declared_as="depends",
    )
    mod = ModInfo(
        id="sample_mod",
        name="Sample Mod",
        version="1.2.3",
        loaders=[Loader.FABRIC],
        provides=["sample_api"],
        dependencies=[dep],
        jar_path="mods/sample.jar",
        metadata_file="fabric.mod.json",
        description="A great mod",
        authors=["Alice", "Bob"],
        license="MIT",
        environment="client",
        parent_jar=None,
        related_files=["config/sample_mod.json"],
        is_nested=False,
    )
    jar = JarFile(
        path="mods/sample.jar",
        size_bytes=1024,
        sha256="abc123def456",
        is_disabled=False,
        mods=["sample_mod"],
        is_nested=False,
        parent_path=None,
    )
    ev = Evidence(
        file="mods/sample.jar",
        field="version",
        raw_value="1.2.3",
        message="Valid version",
    )
    finding = Finding(
        code="SAMPLE_CODE",
        severity=Severity.INFO,
        certainty=Certainty.CERTAIN,
        message="Sample finding message",
        evidence=[ev],
        related_mod_ids=["sample_mod"],
    )

    original = ScanResult(
        scanned_path=".",
        mods_dir="mods",
        jars=[jar],
        mods=[mod],
        findings=[finding],
        detected_loader=Loader.FABRIC,
        detected_mc_version="1.20.1",
        mc_constraints={"=1.20.1": 1},
    )

    as_dict = original.to_dict()
    reconstructed = ScanResult.from_dict(as_dict)

    assert reconstructed == original
    assert reconstructed.to_dict() == as_dict


def test_finding_requires_evidence():
    """Every Finding must have at least one Evidence item."""
    with pytest.raises(ValueError, match="must have at least one Evidence item"):
        Finding(
            code="TEST",
            severity=Severity.ERROR,
            certainty=Certainty.CERTAIN,
            message="No evidence",
            evidence=[],
        )
