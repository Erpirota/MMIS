"""Tests for pure scan snapshot diff engine."""

import json

from mmis.analysis.diff import diff_scans
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


def _make_scan(
    loader: Loader | None = Loader.FABRIC,
    mc_ver: str | None = "1.20.1",
    jars: list[JarFile] | None = None,
    mods: list[ModInfo] | None = None,
    findings: list[Finding] | None = None,
) -> ScanResult:
    return ScanResult(
        scanned_path="/pack",
        mods_dir="/pack/mods",
        jars=jars or [],
        mods=mods or [],
        findings=findings or [],
        detected_loader=loader,
        detected_mc_version=mc_ver,
    )


def test_identical_scans_empty_diff():
    """Identical scans produce an empty diff where has_changes is False."""
    jar = JarFile(path="/pack/mods/mod_a.jar", size_bytes=100, sha256="abc1", is_disabled=False)
    mod = ModInfo(id="mod_a", version="1.0.0", jar_path=jar.path)
    finding = Finding(
        code="TEST_CODE",
        severity=Severity.INFO,
        certainty=Certainty.CERTAIN,
        message="A test finding",
        evidence=[Evidence(file="test.jar")],
    )

    scan_a = _make_scan(jars=[jar], mods=[mod], findings=[finding])
    scan_b = _make_scan(jars=[jar], mods=[mod], findings=[finding])

    diff = diff_scans(scan_a, scan_b)
    assert not diff.has_changes
    assert diff.loader_changed is None
    assert diff.mc_version_changed is None
    assert len(diff.top_level_mods) == 0
    assert len(diff.nested_mods) == 0
    assert len(diff.findings_added) == 0
    assert len(diff.findings_resolved) == 0


def test_loader_and_mc_version_change():
    """Loader and Minecraft version differences are reported."""
    scan_a = _make_scan(loader=Loader.FORGE, mc_ver="1.19.2")
    scan_b = _make_scan(loader=Loader.NEOFORGE, mc_ver="1.20.4")

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes
    assert diff.loader_changed == {"old": "forge", "new": "neoforge"}
    assert diff.mc_version_changed == {"old": "1.19.2", "new": "1.20.4"}


def test_mods_added_and_removed():
    """Added and removed top-level mods are correctly categorized."""
    jar_a = JarFile(path="/pack/mods/old.jar", size_bytes=100, sha256="hash_old", is_disabled=False)
    mod_a = ModInfo(id="old_mod", version="1.0.0", jar_path=jar_a.path)

    jar_b = JarFile(path="/pack/mods/new.jar", size_bytes=200, sha256="hash_new", is_disabled=False)
    mod_b = ModInfo(id="new_mod", version="2.0.0", jar_path=jar_b.path)

    scan_a = _make_scan(jars=[jar_a], mods=[mod_a])
    scan_b = _make_scan(jars=[jar_b], mods=[mod_b])

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes

    assert len(diff.top_level_mods) == 2
    by_id = {m.id: m for m in diff.top_level_mods}

    old_diff = by_id["old_mod"]
    assert old_diff.change_type == "removed"
    assert old_diff.old_version == "1.0.0"
    assert old_diff.new_version is None
    assert old_diff.old_sha256 == "hash_old"
    assert old_diff.new_sha256 is None

    new_diff = by_id["new_mod"]
    assert new_diff.change_type == "added"
    assert new_diff.old_version is None
    assert new_diff.new_version == "2.0.0"
    assert new_diff.new_sha256 == "hash_new"


def test_mod_modifications():
    """Detects version change, sha-only change, jar rename, and enabled/disabled toggle."""
    # Mod 1: Version changed
    jar1_a = JarFile(path="/pack/mods/m1.jar", size_bytes=100, sha256="sha1_a", is_disabled=False)
    mod1_a = ModInfo(id="m1", version="1.0", jar_path=jar1_a.path)
    jar1_b = JarFile(path="/pack/mods/m1.jar", size_bytes=110, sha256="sha1_b", is_disabled=False)
    mod1_b = ModInfo(id="m1", version="1.1", jar_path=jar1_b.path)

    # Mod 2: SHA only changed (recompiled, same version)
    jar2_a = JarFile(path="/pack/mods/m2.jar", size_bytes=100, sha256="sha2_a", is_disabled=False)
    mod2_a = ModInfo(id="m2", version="1.0", jar_path=jar2_a.path)
    jar2_b = JarFile(path="/pack/mods/m2.jar", size_bytes=105, sha256="sha2_b", is_disabled=False)
    mod2_b = ModInfo(id="m2", version="1.0", jar_path=jar2_b.path)

    # Mod 3: Jar renamed
    jar3_a = JarFile(path="/pack/mods/m3-old.jar", size_bytes=100, sha256="sha3", is_disabled=False)
    mod3_a = ModInfo(id="m3", version="1.0", jar_path=jar3_a.path)
    jar3_b = JarFile(path="/pack/mods/m3-new.jar", size_bytes=100, sha256="sha3", is_disabled=False)
    mod3_b = ModInfo(id="m3", version="1.0", jar_path=jar3_b.path)

    # Mod 4: Disabled toggle
    jar4_a = JarFile(path="/pack/mods/m4.jar", size_bytes=100, sha256="sha4", is_disabled=False)
    mod4_a = ModInfo(id="m4", version="1.0", jar_path=jar4_a.path)
    jar4_b = JarFile(path="/pack/mods/m4.jar.disabled", size_bytes=100, sha256="sha4", is_disabled=True)
    mod4_b = ModInfo(id="m4", version="1.0", jar_path=jar4_b.path)

    scan_a = _make_scan(jars=[jar1_a, jar2_a, jar3_a, jar4_a], mods=[mod1_a, mod2_a, mod3_a, mod4_a])
    scan_b = _make_scan(jars=[jar1_b, jar2_b, jar3_b, jar4_b], mods=[mod1_b, mod2_b, mod3_b, mod4_b])

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes
    assert len(diff.top_level_mods) == 4

    mods = {m.id: m for m in diff.top_level_mods}

    assert mods["m1"].change_type == "changed"
    assert mods["m1"].old_version == "1.0"
    assert mods["m1"].new_version == "1.1"

    assert mods["m2"].change_type == "changed"
    assert mods["m2"].old_sha256 == "sha2_a"
    assert mods["m2"].new_sha256 == "sha2_b"

    assert mods["m3"].change_type == "changed"
    assert mods["m3"].old_jar_path == "/pack/mods/m3-old.jar"
    assert mods["m3"].new_jar_path == "/pack/mods/m3-new.jar"

    assert mods["m4"].change_type == "changed"
    assert mods["m4"].old_disabled is False
    assert mods["m4"].new_disabled is True


def test_nested_mods_separation():
    """Nested mods are separated from top-level mods in the diff."""
    parent_jar = JarFile(path="/pack/mods/parent.jar", size_bytes=1000, sha256="parent_sha")
    nested_jar_a = JarFile(path="/pack/mods/parent.jar!/META-INF/jars/n1.jar", size_bytes=200, sha256="n1_sha", is_nested=True)
    mod_top = ModInfo(id="parent_mod", version="1.0.0", jar_path=parent_jar.path, is_nested=False)
    mod_nested_a = ModInfo(id="nested_mod", version="1.0.0", jar_path=nested_jar_a.path, is_nested=True)

    nested_jar_b = JarFile(path="/pack/mods/parent.jar!/META-INF/jars/n1.jar", size_bytes=250, sha256="n1_new_sha", is_nested=True)
    mod_nested_b = ModInfo(id="nested_mod", version="1.2.0", jar_path=nested_jar_b.path, is_nested=True)

    scan_a = _make_scan(jars=[parent_jar, nested_jar_a], mods=[mod_top, mod_nested_a])
    scan_b = _make_scan(jars=[parent_jar, nested_jar_b], mods=[mod_top, mod_nested_b])

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes
    assert len(diff.top_level_mods) == 0
    assert len(diff.nested_mods) == 1
    assert diff.nested_mods[0].id == "nested_mod"
    assert diff.nested_mods[0].is_nested is True
    assert diff.nested_mods[0].old_version == "1.0.0"
    assert diff.nested_mods[0].new_version == "1.2.0"


def test_findings_added_and_resolved():
    """Findings added and resolved are tracked by code, related mods, and evidence files."""
    f1 = Finding(
        code="DUPLICATE_MOD_ID",
        severity=Severity.ERROR,
        certainty=Certainty.CERTAIN,
        message="Duplicate ID foo",
        evidence=[Evidence(file="foo1.jar"), Evidence(file="foo2.jar")],
        related_mod_ids=["foo"],
    )
    f2 = Finding(
        code="UNSATISFIED_DEPENDENCY",
        severity=Severity.WARNING,
        certainty=Certainty.POSSIBLE,
        message="Missing bar",
        evidence=[Evidence(file="bar.jar")],
        related_mod_ids=["bar"],
    )

    # Scan A has f1; Scan B has f2 (so f1 resolved, f2 added)
    scan_a = _make_scan(findings=[f1])
    scan_b = _make_scan(findings=[f2])

    diff = diff_scans(scan_a, scan_b)
    assert diff.has_changes

    assert len(diff.findings_resolved) == 1
    assert diff.findings_resolved[0].code == "DUPLICATE_MOD_ID"
    assert diff.findings_resolved[0].change_type == "resolved"
    assert diff.findings_resolved[0].evidence_files == ["foo1.jar", "foo2.jar"]

    assert len(diff.findings_added) == 1
    assert diff.findings_added[0].code == "UNSATISFIED_DEPENDENCY"
    assert diff.findings_added[0].change_type == "added"
    assert diff.findings_added[0].evidence_files == ["bar.jar"]


def test_diff_to_dict_deterministic():
    """ScanDiff.to_dict() produces deterministic, JSON-serializable output."""
    jar_a = JarFile(path="/pack/mods/b.jar", size_bytes=100, sha256="b_sha")
    jar_b = JarFile(path="/pack/mods/a.jar", size_bytes=100, sha256="a_sha")
    mod_b = ModInfo(id="b_mod", version="1.0.0", jar_path=jar_a.path)
    mod_a = ModInfo(id="a_mod", version="1.0.0", jar_path=jar_b.path)

    scan_a = _make_scan(mods=[mod_b, mod_a], jars=[jar_a, jar_b])
    scan_b = _make_scan(mods=[], jars=[])

    diff = diff_scans(scan_a, scan_b)
    d = diff.to_dict()

    json_str1 = json.dumps(d, sort_keys=True, indent=2)
    json_str2 = json.dumps(d, sort_keys=True, indent=2)
    assert json_str1 == json_str2

    # Verify sorting in dict output
    assert [m["id"] for m in d["top_level_mods"]] == ["a_mod", "b_mod"]
