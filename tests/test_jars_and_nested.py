"""Tests for jar archive reading, dual metadata, nested jars, limits, and error handling."""

from pathlib import Path
from mmis.domain.findings import (
    CORRUPT_JAR,
    LEGACY_MOD_INFO,
    NESTED_JAR_DEPTH_EXCEEDED,
    NESTED_JAR_SIZE_EXCEEDED,
    NO_METADATA,
    NOT_A_ZIP,
)
from mmis.domain.models import Loader
from mmis.minecraft.jar import MAX_NESTED_SIZE_BYTES, read_jar
from mmis.minecraft.scanner import scan
from tests.helpers import create_jar, create_nested_jar_bytes


def test_dual_format_jar(tmp_path: Path):
    """Jar containing both fabric.mod.json and META-INF/mods.toml."""
    fabric_json = '{"schemaVersion": 1, "id": "dual_mod", "name": "Dual Mod Fabric"}'
    mods_toml = """
    [[mods]]
    modId = "dual_mod"
    displayName = "Dual Mod Forge"
    version = "1.0.0"
    """
    jar_bytes = create_nested_jar_bytes({
        "fabric.mod.json": fabric_json,
        "META-INF/mods.toml": mods_toml,
    })
    j_file, mods, findings, nested = read_jar(jar_bytes, "mods/dual.jar")

    assert len(mods) == 2
    assert mods[0].id == "dual_mod"
    assert mods[0].loaders == [Loader.FABRIC]
    assert mods[1].id == "dual_mod"
    assert mods[1].loaders == [Loader.FORGE]
    assert j_file.mods == ["dual_mod", "dual_mod"]

    # Also assert at scan() level
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(parents=True)
    jar_path = mods_dir / "dual.jar"
    jar_path.write_bytes(jar_bytes)

    res = scan(str(tmp_path))
    assert len(res.mods) == 2
    assert res.mods[0].id == "dual_mod"
    assert res.mods[0].loaders == [Loader.FABRIC]
    assert res.mods[1].id == "dual_mod"
    assert res.mods[1].loaders == [Loader.FORGE]
    assert res.jars[0].mods == ["dual_mod", "dual_mod"]


def test_nested_jars_discovery(tmp_path: Path):
    """Parent jar with nested Fabric and Forge jars."""
    nested_fabric_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "nested_fabric", "version": "1.0"}'
    })
    parent_bytes = create_nested_jar_bytes({
        "fabric.mod.json": """{
            "schemaVersion": 1,
            "id": "parent_mod",
            "jars": [{"file": "META-INF/jars/sub_fabric.jar"}]
        }""",
        "META-INF/jars/sub_fabric.jar": nested_fabric_bytes,
    })

    j_file, mods, findings, nested = read_jar(parent_bytes, "mods/parent.jar")
    assert len(mods) == 1
    assert mods[0].id == "parent_mod"
    assert len(nested) == 1

    sub_display_path, sub_bytes = nested[0]
    assert sub_display_path == "mods/parent.jar!/META-INF/jars/sub_fabric.jar"

    # Read nested jar
    sub_j_file, sub_mods, sub_findings, sub_nested = read_jar(
        sub_bytes,
        display_path=sub_display_path,
        parent_path="mods/parent.jar",
        depth=1,
    )
    assert len(sub_mods) == 1
    assert sub_mods[0].id == "nested_fabric"
    assert sub_mods[0].parent_jar == "mods/parent.jar"
    assert sub_mods[0].is_nested is True


def test_nested_jar_depth_limit():
    """Exceeding max depth 3 produces finding without failure."""
    nested_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "deep_mod"}'
    })
    parent_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "root_mod", "jars": [{"file": "META-INF/jars/deep.jar"}]}',
        "META-INF/jars/deep.jar": nested_bytes,
    })

    # Call with depth = 3 -> depth + 1 = 4 > 3
    j_file, mods, findings, nested = read_jar(parent_bytes, "mods/root.jar", depth=3)
    assert any(f.code == NESTED_JAR_DEPTH_EXCEEDED for f in findings)
    assert len(nested) == 0


def test_nested_jar_size_limit(monkeypatch):
    """Nested jar entry exceeding size limit yields finding."""
    monkeypatch.setattr("mmis.minecraft.jar.MAX_NESTED_SIZE_BYTES", 50)
    nested_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_large", "name": "Large Mod With Description Over 50 Bytes"}'
    })
    parent_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "root_mod", "jars": [{"file": "META-INF/jars/large.jar"}]}',
        "META-INF/jars/large.jar": nested_bytes,
    })

    j_file, mods, findings, nested = read_jar(parent_bytes, "mods/parent.jar")
    assert any(f.code == NESTED_JAR_SIZE_EXCEEDED for f in findings)
    assert len(nested) == 0


def test_non_zip_file():
    data = b"This is just a text file, not a zip."
    j_file, mods, findings, nested = read_jar(data, "mods/fake.jar")
    assert len(mods) == 0
    assert any(f.code == NOT_A_ZIP for f in findings)


def test_corrupt_zip_file():
    # Valid zip header truncated
    data = b"PK\x03\x04" + b"\x00" * 10
    j_file, mods, findings, nested = read_jar(data, "mods/corrupt.jar")
    assert len(mods) == 0
    assert any(f.code == CORRUPT_JAR for f in findings)


def test_empty_jar():
    data = create_nested_jar_bytes({})
    j_file, mods, findings, nested = read_jar(data, "mods/empty.jar")
    assert len(mods) == 0
    assert any(f.code == NO_METADATA for f in findings)


def test_legacy_mcmod_info_only():
    data = create_nested_jar_bytes({
        "mcmod.info": '[{"modid": "legacy_mod", "name": "Legacy Mod"}]'
    })
    j_file, mods, findings, nested = read_jar(data, "mods/legacy.jar")
    assert len(mods) == 0
    assert any(f.code == LEGACY_MOD_INFO for f in findings)
