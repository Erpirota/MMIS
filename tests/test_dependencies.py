"""Tests for dependency analyzer covering Acceptance Criteria A1, A3."""

import pytest
from mmis.analysis.dependencies import analyze_dependencies
from mmis.domain.findings import (
    DEP_CYCLE,
    DEP_DISABLED_PROVIDER,
    DEP_MISSING_OPTIONAL,
    DEP_MISSING_REQUIRED,
    DEP_OPTIONAL_MISMATCH,
    DEP_VERSION_MISMATCH,
    DEP_VERSION_UNDETERMINED,
    PLATFORM_DEP_UNVERIFIED,
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


def _make_scan(mods: list[ModInfo], jars: list[JarFile] | None = None, detected_loader: Loader | None = Loader.FABRIC, mc_ver: str | None = "1.20.1") -> ScanResult:
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
        detected_loader=detected_loader,
        detected_mc_version=mc_ver,
    )


def test_dependency_satisfied_direct():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[
            Dependency(mod_id="mod_b", version_range=VersionRange.parse(">=1.0.0", syntax="fabric")),
        ],
    )
    m2 = ModInfo(
        id="mod_b",
        version="1.2.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/b.jar",
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    # mod_b satisfies mod_a, no error findings
    assert not any(f.code in (DEP_MISSING_REQUIRED, DEP_VERSION_MISMATCH) for f in findings)


def test_dependency_satisfied_via_provides():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[
            Dependency(mod_id="common_api", version_range=VersionRange.parse(">=2.0.0", syntax="fabric")),
        ],
    )
    m2 = ModInfo(
        id="mod_provider",
        version="2.1.0",
        provides=["common_api"],
        loaders=[Loader.FABRIC],
        jar_path="mods/prov.jar",
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    assert not any(f.code == DEP_MISSING_REQUIRED for f in findings)


def test_dependency_satisfied_by_nested_jar():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[Dependency(mod_id="nested_lib", version_range=VersionRange.parse("1.0.0", syntax="fabric"))],
    )
    m2 = ModInfo(
        id="nested_lib",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar!/META-INF/jars/lib.jar",
        is_nested=True,
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    assert not any(f.code == DEP_MISSING_REQUIRED for f in findings)


def test_dependency_missing_required():
    m1 = ModInfo(
        id="mod_a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[Dependency(mod_id="missing_mod", version_range=VersionRange.parse(">=1.0.0", syntax="fabric"))],
    )
    scan = _make_scan([m1])
    findings = analyze_dependencies(scan)
    missing = [f for f in findings if f.code == DEP_MISSING_REQUIRED]
    assert len(missing) == 1
    f = missing[0]
    assert f.severity == Severity.ERROR
    assert f.certainty == Certainty.CERTAIN
    assert f.related_mod_ids == ["mod_a", "missing_mod"]
    assert f.suggestion is not None and len(f.suggestion) > 0
    assert len(f.evidence) >= 1
    assert get_category(f.code) == "dependency"


def test_dependency_version_mismatch():
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[Dependency(mod_id="beta", version_range=VersionRange.parse(">=2.0.0", syntax="fabric"))],
    )
    m2 = ModInfo(
        id="beta",
        version="1.4.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/beta.jar",
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    mismatch = [f for f in findings if f.code == DEP_VERSION_MISMATCH]
    assert len(mismatch) == 1
    f = mismatch[0]
    assert f.severity == Severity.ERROR
    assert f.certainty == Certainty.CERTAIN
    assert "alpha requires beta >=2.0.0, but beta 1.4.0 is installed" in f.message
    assert f.related_mod_ids == ["alpha", "beta"]
    assert f.suggestion is not None


def test_dependency_version_undetermined():
    vr = VersionRange(raw="foo-custom-range", syntax="fabric", parsed=None, is_valid=False)
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[Dependency(mod_id="beta", version_range=vr)],
    )
    m2 = ModInfo(
        id="beta",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/beta.jar",
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    undet = [f for f in findings if f.code == DEP_VERSION_UNDETERMINED]
    assert len(undet) == 1
    assert undet[0].severity == Severity.INFO
    assert undet[0].certainty == Certainty.INSUFFICIENT_DATA


def test_dependency_provider_in_disabled_jar():
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[Dependency(mod_id="beta", version_range=VersionRange.parse("1.0.0", syntax="fabric"))],
    )
    m2 = ModInfo(
        id="beta",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/beta.jar.disabled",
    )
    jars = [
        JarFile(path="mods/alpha.jar", size_bytes=100, sha256="1", is_disabled=False, mods=["alpha"]),
        JarFile(path="mods/beta.jar.disabled", size_bytes=100, sha256="2", is_disabled=True, mods=["beta"]),
    ]
    scan = _make_scan([m1, m2], jars=jars)
    findings = analyze_dependencies(scan)
    dis = [f for f in findings if f.code == DEP_DISABLED_PROVIDER]
    assert len(dis) == 1
    assert dis[0].severity == Severity.ERROR
    assert dis[0].certainty == Certainty.CERTAIN
    assert dis[0].related_mod_ids == ["alpha", "beta"]
    assert dis[0].suggestion is not None


def test_dependency_missing_optional_and_mismatch():
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[
            Dependency(mod_id="opt_missing", version_range=VersionRange.parse(">=1.0.0", syntax="fabric"), kind=DependencyKind.OPTIONAL),
            Dependency(mod_id="opt_mismatch", version_range=VersionRange.parse(">=2.0.0", syntax="fabric"), kind=DependencyKind.OPTIONAL),
        ],
    )
    m2 = ModInfo(
        id="opt_mismatch",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/opt.jar",
    )
    scan = _make_scan([m1, m2])
    findings = analyze_dependencies(scan)
    missing = [f for f in findings if f.code == DEP_MISSING_OPTIONAL]
    assert len(missing) == 1
    assert missing[0].severity == Severity.INFO

    mismatch = [f for f in findings if f.code == DEP_OPTIONAL_MISMATCH]
    assert len(mismatch) == 1
    assert mismatch[0].severity == Severity.WARNING
    assert mismatch[0].certainty == Certainty.CERTAIN


def test_dependency_platform_ids_never_missing():
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[
            Dependency(mod_id="java", version_range=VersionRange.parse(">=17", syntax="fabric")),
            Dependency(mod_id="fabricloader", version_range=VersionRange.parse(">=0.14.0", syntax="fabric")),
            Dependency(mod_id="minecraft", version_range=VersionRange.parse(">=1.20.1", syntax="fabric")),
        ],
    )
    scan = _make_scan([m1], mc_ver="1.20.1")
    findings = analyze_dependencies(scan)
    # Platform ids must NEVER produce DEP_MISSING_REQUIRED
    assert not any(f.code == DEP_MISSING_REQUIRED for f in findings)
    # Exactly one aggregated finding for unverified platform deps (java, fabricloader)
    plat = [f for f in findings if f.code == PLATFORM_DEP_UNVERIFIED]
    assert len(plat) == 1
    assert plat[0].severity == Severity.INFO
    assert plat[0].certainty == Certainty.INSUFFICIENT_DATA


def test_dependency_self_dependency_ignored():
    m1 = ModInfo(
        id="alpha",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/alpha.jar",
        dependencies=[
            Dependency(mod_id="alpha", version_range=VersionRange.parse("1.0.0", syntax="fabric")),
        ],
    )
    scan = _make_scan([m1])
    findings = analyze_dependencies(scan)
    assert not any(f.code in (DEP_MISSING_REQUIRED, DEP_CYCLE) for f in findings)


def test_dependency_cycles_2_and_3():
    # 2-cycle: a -> b, b -> a
    a = ModInfo(
        id="a",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/a.jar",
        dependencies=[Dependency(mod_id="b", kind=DependencyKind.REQUIRED)],
    )
    b = ModInfo(
        id="b",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path="mods/b.jar",
        dependencies=[Dependency(mod_id="a", kind=DependencyKind.REQUIRED)],
    )
    scan2 = _make_scan([a, b])
    findings2 = analyze_dependencies(scan2)
    cycles2 = [f for f in findings2 if f.code == DEP_CYCLE]
    assert len(cycles2) == 1
    assert cycles2[0].severity == Severity.WARNING
    assert cycles2[0].certainty == Certainty.CERTAIN
    assert "a -> b -> a" in cycles2[0].message
    assert cycles2[0].related_mod_ids == ["a", "b"]

    # 3-cycle: x -> y, y -> z, z -> x
    x = ModInfo(id="x", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/x.jar", dependencies=[Dependency(mod_id="y")])
    y = ModInfo(id="y", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/y.jar", dependencies=[Dependency(mod_id="z")])
    z = ModInfo(id="z", version="1.0.0", loaders=[Loader.FABRIC], jar_path="mods/z.jar", dependencies=[Dependency(mod_id="x")])
    scan3 = _make_scan([x, y, z])
    findings3 = analyze_dependencies(scan3)
    cycles3 = [f for f in findings3 if f.code == DEP_CYCLE]
    assert len(cycles3) == 1
    assert "x -> y -> z -> x" in cycles3[0].message
    assert cycles3[0].related_mod_ids == ["x", "y", "z"]


def test_inactive_record_in_dual_jar_ignored(tmp_path):
    """Forge metadata in a dual-format jar is inactive in a Fabric pack and contributes nothing."""
    from mmis.analysis.engine import analyze_pack
    from mmis.minecraft.scanner import scan
    from tests.helpers import create_jar

    # Create CurseForge manifest for Fabric pack
    manifest = {
        "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-0.15.11", "primary": True}]},
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "Fabric Test Pack",
        "version": "1.0.0",
        "author": "tester",
        "files": [],
    }
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()

    # a.jar has fabric dependencies on java and fabricloader
    a_fabric_json = json.dumps({
        "schemaVersion": 1,
        "id": "a",
        "version": "1.0.0",
        "depends": {"fabricloader": ">=0.14.0", "java": ">=17"},
    })
    create_jar(mods_dir / "a.jar", {"fabric.mod.json": a_fabric_json})

    # multi.jar has fabric.mod.json (no deps) AND mods.toml with dependency on forge [47,)
    multi_fabric_json = json.dumps({
        "schemaVersion": 1,
        "id": "multi",
        "version": "1.0.0",
    })
    multi_forge_toml = """
    [[mods]]
    modId = "multi"
    version = "1.0.0"
    [[dependencies.multi]]
    modId = "forge"
    mandatory = true
    versionRange = "[47,)"
    """
    create_jar(mods_dir / "multi.jar", {
        "fabric.mod.json": multi_fabric_json,
        "META-INF/mods.toml": multi_forge_toml,
    })

    scan_res = scan(str(tmp_path))
    assert scan_res.detected_loader == Loader.FABRIC

    report = analyze_pack(scan_res)
    plat_findings = [f for f in report.findings if f.code == PLATFORM_DEP_UNVERIFIED]
    assert len(plat_findings) == 1
    plat = plat_findings[0]
    # Only 2 platform dependencies (from a: fabricloader + java), multi's forge dep is inactive
    assert len(plat.evidence) == 2
    assert plat.related_mod_ids == ["a"]
    assert "multi" not in plat.related_mod_ids

