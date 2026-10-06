"""Tests for loader and Minecraft version derivation rules 5a-5c."""

import json
from pathlib import Path

from mmis.domain.findings import (
    AMBIGUOUS_LOADER,
    AMBIGUOUS_MC_VERSION,
    CONFLICTING_MANIFEST,
    PACK_MANIFEST_DETECTED,
    UNKNOWN_LOADER,
)
from mmis.domain.models import Loader
from mmis.minecraft.scanner import scan
from tests.helpers import create_jar


def test_rule_5a_curseforge_manifest(tmp_path: Path):
    cf_data = {
        "minecraft": {
            "version": "1.20.1",
            "modLoaders": [{"id": "forge-47.1.0", "primary": True}],
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(cf_data), encoding="utf-8")
    res = scan(str(tmp_path))

    assert res.detected_loader == Loader.FORGE
    assert res.detected_mc_version == "1.20.1"
    assert any(f.code == PACK_MANIFEST_DETECTED for f in res.findings)


def test_rule_5a_modrinth_manifest(tmp_path: Path):
    mr_data = {
        "formatVersion": 1,
        "dependencies": {
            "minecraft": "1.20.4",
            "fabric-loader": "0.15.7",
        },
    }
    (tmp_path / "modrinth.index.json").write_text(json.dumps(mr_data), encoding="utf-8")
    res = scan(str(tmp_path))

    assert res.detected_loader == Loader.FABRIC
    assert res.detected_mc_version == "1.20.4"


def test_rule_5a_multimc_manifest(tmp_path: Path):
    mmc_data = {
        "formatVersion": 1,
        "components": [
            {"uid": "net.minecraft", "version": "1.20.2"},
            {"uid": "net.neoforged", "version": "20.2.86"},
        ],
    }
    (tmp_path / "mmc-pack.json").write_text(json.dumps(mmc_data), encoding="utf-8")
    res = scan(str(tmp_path))

    assert res.detected_loader == Loader.NEOFORGE
    assert res.detected_mc_version == "1.20.2"


def test_rule_5a_conflicting_manifests(tmp_path: Path):
    cf_data = {
        "minecraft": {
            "version": "1.20.1",
            "modLoaders": [{"id": "forge-47.1.0", "primary": True}],
        }
    }
    mr_data = {
        "dependencies": {
            "minecraft": "1.20.2",
            "fabric-loader": "0.15.7",
        }
    }
    (tmp_path / "manifest.json").write_text(json.dumps(cf_data), encoding="utf-8")
    (tmp_path / "modrinth.index.json").write_text(json.dumps(mr_data), encoding="utf-8")

    res = scan(str(tmp_path))
    assert any(f.code == CONFLICTING_MANIFEST for f in res.findings)


def test_rule_5b_fabric_consensus(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a"}'
    })
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_b"}'
    })
    res = scan(str(tmp_path))
    assert res.detected_loader == Loader.FABRIC


def test_rule_5b_quilt_with_fabric(tmp_path: Path):
    """Fabric mod + Quilt mod resolves to Quilt."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_fabric.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_fabric"}'
    })
    create_jar(mods_dir / "mod_quilt.jar", {
        "quilt.mod.json": '{"schema_version": 1, "quilt_loader": {"id": "mod_quilt"}}'
    })
    res = scan(str(tmp_path))
    assert res.detected_loader == Loader.QUILT


def test_rule_5b_ambiguous_forge_and_fabric(tmp_path: Path):
    """Incompatible loaders across jars produces AMBIGUOUS_LOADER finding."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_fabric.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_fabric"}'
    })
    create_jar(mods_dir / "mod_forge.jar", {
        "META-INF/mods.toml": '[[mods]]\nmodId = "mod_forge"'
    })
    res = scan(str(tmp_path))
    assert res.detected_loader is None
    assert any(f.code == AMBIGUOUS_LOADER for f in res.findings)


def test_rule_5b_disabled_jar_excluded(tmp_path: Path):
    """Disabled Forge jar does not conflict with enabled Fabric jar."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_fabric.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_fabric"}'
    })
    create_jar(mods_dir / "mod_forge.jar.disabled", {
        "META-INF/mods.toml": '[[mods]]\nmodId = "mod_forge"'
    })
    res = scan(str(tmp_path))
    assert res.detected_loader == Loader.FABRIC
    disabled_jars = [j for j in res.jars if j.is_disabled]
    assert len(disabled_jars) == 1


def test_rule_5b_unknown_loader(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "empty.jar", {})
    res = scan(str(tmp_path))
    assert res.detected_loader is None
    assert any(f.code == UNKNOWN_LOADER for f in res.findings)


def test_rule_5c_exact_pinned_mc_version(tmp_path: Path):
    """One mod pins exact 1.20.1, another declares >=1.20."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a", "depends": {"minecraft": "=1.20.1"}}'
    })
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_b", "depends": {"minecraft": ">=1.20"}}'
    })
    res = scan(str(tmp_path))
    assert res.detected_mc_version == "1.20.1"
    assert res.mc_constraints["=1.20.1"] == 1
    assert res.mc_constraints[">=1.20"] == 1


def test_rule_5c_ranges_only_no_exact_guess(tmp_path: Path):
    """Only ranges declared: detected_mc_version must stay None (never guess consensus)."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a", "depends": {"minecraft": ">=1.20.1"}}'
    })
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_b", "depends": {"minecraft": "~1.20"}}'
    })
    res = scan(str(tmp_path))
    assert res.detected_mc_version is None
    assert len(res.mc_constraints) == 2


def test_rule_5c_conflicting_exact_versions(tmp_path: Path):
    """Conflicting exact versions declared -> None + finding."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a", "depends": {"minecraft": "=1.20.1"}}'
    })
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_b", "depends": {"minecraft": "=1.20.2"}}'
    })
    res = scan(str(tmp_path))
    assert res.detected_mc_version is None
    assert any(f.code == AMBIGUOUS_MC_VERSION for f in res.findings)


def test_related_config_files(tmp_path: Path):
    """Config files matching mod ID are attached to ModInfo.related_files."""
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mymod.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mymod"}'
    })
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "mymod.json").write_text("{}", encoding="utf-8")
    (config_dir / "mymod-extra.toml").write_text("", encoding="utf-8")
    sub_dir = config_dir / "mymod"
    sub_dir.mkdir()
    (sub_dir / "nested_setting.cfg").write_text("", encoding="utf-8")

    res = scan(str(tmp_path))
    assert len(res.mods) == 1
    m = res.mods[0]
    assert len(m.related_files) == 3
    assert "config/mymod.json" in m.related_files
    assert "config/mymod-extra.toml" in m.related_files
    assert "config/mymod/nested_setting.cfg" in m.related_files
