"""Tests for conflict analyzer covering Acceptance Criteria A2, A3."""

import pytest
from mmis.analysis.conflicts import analyze_conflicts
from mmis.domain.findings import (
    CONFLICT_BREAKS_DECLARED,
    CONFLICT_DUPLICATE_MOD,
    CONFLICT_LOADER_MISMATCH,
    CONFLICT_MC_VERSION_MISMATCH,
    CONFLICT_MULTIPLE_PROVIDERS,
    CONFLICT_SOFT_DISCOURAGED,
    CONFLICT_UNANALYZABLE_JARS,
    CORRUPT_JAR,
    get_category,
)
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


def _make_scan(
    mods: list[ModInfo],
    jars: list[JarFile] | None = None,
    detected_loader: Loader | None = Loader.FABRIC,
    detected_mc_version: str | None = "1.20.1",
    findings: list[Finding] | None = None,
) -> ScanResult:
    if jars is None:
        jars = [
            JarFile(
                path=m.jar_path or "mods/test.jar",
                size_bytes=1000,
                sha256="abc",
                is_disabled=False,
                mods=[m.id],
            )
            for m in mods
        ]
    return ScanResult(
        scanned_path=".",
        mods_dir="mods",
        jars=jars,
        mods=mods,
        findings=findings or [],
        detected_loader=detected_loader,
        detected_mc_version=detected_mc_version,
    )


# --- 1. CONFLICT_DUPLICATE_MOD ---
def test_duplicate_mod_positive():
    m1 = ModInfo(id="foo", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/foo-1.jar")
    m2 = ModInfo(id="foo", version="1.1.0", loaders=[Loader.FABRIC], jar_path="mods/foo-2.jar")
    jars = [
        JarFile(path="mods/foo-1.jar", size_bytes=100, sha256="1", is_disabled=False, mods=["foo"]),
        JarFile(path="mods/foo-2.jar", size_bytes=100, sha256="2", is_disabled=False, mods=["foo"]),
    ]
    scan = _make_scan([m1, m2], jars=jars)
    findings = analyze_conflicts(scan)
    dups = [f for f in findings if f.code == CONFLICT_DUPLICATE_MOD]
    assert len(dups) == 1
    assert dups[0].severity == Severity.ERROR
    assert dups[0].certainty == Certainty.CERTAIN
    assert dups[0].related_mod_ids == ["foo"]
    assert dups[0].suggestion is not None
    assert get_category(dups[0].code) == "conflict"


def test_duplicate_mod_negative_nested_and_same_jar():
    # Nested duplicate is normal JiJ -> NO finding
    top = ModInfo(id="foo", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/foo.jar", is_nested=False)
    nested = ModInfo(id="foo", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/bar.jar!/lib/foo.jar", is_nested=True)
    # Same mod id twice from SAME jar (multi-format) -> NO finding
    same_jar1 = ModInfo(id="multi", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/multi.jar")
    same_jar2 = ModInfo(id="multi", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/multi.jar")

    jars = [
        JarFile(path="mods/foo.jar", size_bytes=100, sha256="1", is_disabled=False, mods=["foo"]),
        JarFile(path="mods/bar.jar!/lib/foo.jar", size_bytes=50, sha256="2", is_disabled=False, mods=["foo"], is_nested=True),
        JarFile(path="mods/multi.jar", size_bytes=100, sha256="3", is_disabled=False, mods=["multi"]),
    ]
    scan = _make_scan([top, nested, same_jar1, same_jar2], jars=jars, detected_loader=Loader.FABRIC)
    findings = analyze_conflicts(scan)
    assert not any(f.code == CONFLICT_DUPLICATE_MOD for f in findings)


# --- 2. CONFLICT_BREAKS_DECLARED ---
def test_breaks_positive_certain():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[
            Dependency(mod_id="mod_b", version_range=VersionRange.parse(">=1.0.0", syntax="fabric"), kind=DependencyKind.BREAKS),
        ],
    )
    m2 = ModInfo(id="mod_b", version="1.5.0", loaders=[Loader.FABRIC], jar_path="mods/b.jar")
    scan = _make_scan([m1, m2])
    findings = analyze_conflicts(scan)
    breaks = [f for f in findings if f.code == CONFLICT_BREAKS_DECLARED]
    assert len(breaks) == 1
    assert breaks[0].certainty == Certainty.CERTAIN
    assert breaks[0].severity == Severity.ERROR
    assert breaks[0].related_mod_ids == ["mod_a", "mod_b"]


def test_breaks_negative_non_matching_version():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[
            Dependency(mod_id="mod_b", version_range=VersionRange.parse("<1.0.0", syntax="fabric"), kind=DependencyKind.BREAKS),
        ],
    )
    m2 = ModInfo(id="mod_b", version="1.5.0", loaders=[Loader.FABRIC], jar_path="mods/b.jar")
    scan = _make_scan([m1, m2])
    findings = analyze_conflicts(scan)
    assert not any(f.code == CONFLICT_BREAKS_DECLARED for f in findings)


def test_breaks_positive_undetermined_possible():
    vr = VersionRange(raw="invalid-range", syntax="fabric", parsed=None, is_valid=False)
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[Dependency(mod_id="mod_b", version_range=vr, kind=DependencyKind.BREAKS)],
    )
    m2 = ModInfo(id="mod_b", version="1.5.0", loaders=[Loader.FABRIC], jar_path="mods/b.jar")
    scan = _make_scan([m1, m2])
    findings = analyze_conflicts(scan)
    breaks = [f for f in findings if f.code == CONFLICT_BREAKS_DECLARED]
    assert len(breaks) == 1
    assert breaks[0].certainty == Certainty.POSSIBLE
    assert breaks[0].severity == Severity.WARNING


# --- 3. CONFLICT_SOFT_DISCOURAGED ---
def test_soft_conflicts_positive_and_negative():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[
            Dependency(mod_id="mod_present", kind=DependencyKind.CONFLICTS),
            Dependency(mod_id="mod_absent", kind=DependencyKind.CONFLICTS),
        ],
    )
    m2 = ModInfo(id="mod_present", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/p.jar")
    scan = _make_scan([m1, m2])
    findings = analyze_conflicts(scan)
    soft = [f for f in findings if f.code == CONFLICT_SOFT_DISCOURAGED]
    assert len(soft) == 1
    assert soft[0].certainty == Certainty.POSSIBLE
    assert soft[0].related_mod_ids == ["mod_a", "mod_present"]


# --- 4. CONFLICT_LOADER_MISMATCH ---
def test_loader_mismatch_positive_certain():
    forge_mod = ModInfo(id="forge_only", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/forge.jar")
    scan = _make_scan([forge_mod], detected_loader=Loader.FABRIC)
    findings = analyze_conflicts(scan)
    mismatches = [f for f in findings if f.code == CONFLICT_LOADER_MISMATCH]
    assert len(mismatches) == 1
    assert mismatches[0].certainty == Certainty.CERTAIN
    assert mismatches[0].severity == Severity.ERROR


def test_loader_mismatch_negative_multiloader_jar():
    # Jar carries both Fabric and Forge metadata in Fabric pack: active record exists -> NO mismatch finding
    m_fab = ModInfo(id="multi", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/multi.jar")
    m_frg = ModInfo(id="multi", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/multi.jar")
    scan = _make_scan([m_fab, m_frg], detected_loader=Loader.FABRIC)
    findings = analyze_conflicts(scan)
    assert not any(f.code == CONFLICT_LOADER_MISMATCH for f in findings)


def test_loader_mismatch_negative_unknown_loader():
    forge_mod = ModInfo(id="forge_only", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/forge.jar")
    scan = _make_scan([forge_mod], detected_loader=None)
    findings = analyze_conflicts(scan)
    assert not any(f.code == CONFLICT_LOADER_MISMATCH for f in findings)


def test_loader_mismatch_negative_disabled_jar():
    forge_mod = ModInfo(id="forge_only", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/forge.jar.disabled")
    jars = [JarFile(path="mods/forge.jar.disabled", size_bytes=100, sha256="1", is_disabled=True, mods=["forge_only"])]
    scan = _make_scan([forge_mod], jars=jars, detected_loader=Loader.FABRIC)
    findings = analyze_conflicts(scan)
    assert not any(f.code == CONFLICT_LOADER_MISMATCH for f in findings)


def test_loader_mismatch_forge_in_neoforge_possible():
    forge_mod = ModInfo(id="forge_mod", version="1.0.0", loaders=[Loader.FORGE], jar_path="mods/forge.jar")
    scan = _make_scan([forge_mod], detected_loader=Loader.NEOFORGE)
    findings = analyze_conflicts(scan)
    mismatches = [f for f in findings if f.code == CONFLICT_LOADER_MISMATCH]
    assert len(mismatches) == 1
    assert mismatches[0].certainty == Certainty.POSSIBLE
    assert mismatches[0].severity == Severity.WARNING


# --- 5. CONFLICT_MC_VERSION_MISMATCH ---
def test_mc_version_mismatch_positive_and_negative():
    m_bad = ModInfo(
        id="old_mod",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/old.jar",
        dependencies=[Dependency(mod_id="minecraft", version_range=VersionRange.parse("<=1.19.2", syntax="fabric"))],
    )
    m_good = ModInfo(
        id="good_mod",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/good.jar",
        dependencies=[Dependency(mod_id="minecraft", version_range=VersionRange.parse(">=1.20.1", syntax="fabric"))],
    )
    scan = _make_scan([m_bad, m_good], detected_mc_version="1.20.1")
    findings = analyze_conflicts(scan)
    mc_mismatch = [f for f in findings if f.code == CONFLICT_MC_VERSION_MISMATCH]
    assert len(mc_mismatch) == 1
    assert mc_mismatch[0].certainty == Certainty.CERTAIN
    assert mc_mismatch[0].related_mod_ids == ["old_mod", "minecraft"]


# --- 6. CONFLICT_MULTIPLE_PROVIDERS ---
def test_multiple_providers_positive_and_negative():
    m1 = ModInfo(id="mod_1", version="1.0.0", provides=["custom_api"], loaders=[Loader.FABRIC], jar_path="mods/1.jar")
    m2 = ModInfo(id="mod_2", version="1.0.0", provides=["custom_api"], loaders=[Loader.FABRIC], jar_path="mods/2.jar")
    m3 = ModInfo(id="mod_3", version="1.0.0", provides=["unique_api"], loaders=[Loader.FABRIC], jar_path="mods/3.jar")
    scan = _make_scan([m1, m2, m3])
    findings = analyze_conflicts(scan)
    prov_conflicts = [f for f in findings if f.code == CONFLICT_MULTIPLE_PROVIDERS]
    assert len(prov_conflicts) == 1
    assert prov_conflicts[0].certainty == Certainty.POSSIBLE
    assert "custom_api" in prov_conflicts[0].message
    assert sorted(prov_conflicts[0].related_mod_ids) == ["mod_1", "mod_2"]


# --- 7. CONFLICT_UNANALYZABLE_JARS ---
def test_unanalyzable_jars_positive_and_negative():
    clean_scan = _make_scan([])
    assert not any(f.code == CONFLICT_UNANALYZABLE_JARS for f in analyze_conflicts(clean_scan))

    corrupt_ev = Evidence(file="mods/broken.jar", message="Corrupt zip file")
    corrupt_finding = Finding(code=CORRUPT_JAR, severity=Severity.ERROR, certainty=Certainty.CERTAIN, message="Broken", evidence=[corrupt_ev])
    dirty_scan = _make_scan([], findings=[corrupt_finding])
    findings = analyze_conflicts(dirty_scan)
    unanalyzable = [f for f in findings if f.code == CONFLICT_UNANALYZABLE_JARS]
    assert len(unanalyzable) == 1
    assert unanalyzable[0].severity == Severity.INFO
    assert unanalyzable[0].certainty == Certainty.INSUFFICIENT_DATA
    assert len(unanalyzable[0].evidence) == 1
    assert unanalyzable[0].suggestion is not None
