"""Pure function tests for Fabric, Quilt, Forge, and NeoForge parsers."""

from mmis.domain.findings import (
    INVALID_METADATA_JSON,
    INVALID_METADATA_TOML,
    INVALID_VERSION_RANGE,
    LENIENT_JSON_REPAIRED,
    UNRESOLVED_PLACEHOLDER,
)
from mmis.domain.models import DependencyKind, Loader
from mmis.minecraft.fabric import parse_fabric_mod_json
from mmis.minecraft.forge import parse_forge_toml
from mmis.minecraft.quilt import parse_quilt_mod_json


def test_fabric_parser_full():
    content = """{
        "schemaVersion": 1,
        "id": "test_fabric",
        "name": "Test Fabric Mod",
        "version": "1.0.0",
        "description": "A fabric mod",
        "authors": ["Author One", {"name": "Author Two"}],
        "license": "MIT",
        "environment": "client",
        "provides": ["provided_api"],
        "depends": {
            "fabricloader": ">=0.14.0",
            "minecraft": "~1.20"
        },
        "recommends": {
            "opt_mod": "*"
        },
        "breaks": {
            "bad_mod": "*"
        },
        "jars": [
            {"file": "META-INF/jars/nested.jar"}
        ]
    }"""
    mods, findings, nested = parse_fabric_mod_json(content, jar_path="mods/test.jar")
    assert len(mods) == 1
    m = mods[0]
    assert m.id == "test_fabric"
    assert m.name == "Test Fabric Mod"
    assert m.version == "1.0.0"
    assert m.loaders == [Loader.FABRIC]
    assert m.license == "MIT"
    assert m.environment == "client"
    assert "provided_api" in m.provides
    assert len(m.authors) == 2
    assert "nested.jar" in nested[0]

    dep_kinds = {d.mod_id: d.kind for d in m.dependencies}
    assert dep_kinds["fabricloader"] == DependencyKind.REQUIRED
    assert dep_kinds["opt_mod"] == DependencyKind.OPTIONAL
    assert dep_kinds["bad_mod"] == DependencyKind.BREAKS


def test_fabric_lenient_json():
    # Raw newline and raw tab inside description string
    content = """{
        "schemaVersion": 1,
        "id": "lenient_mod",
        "version": "1.0.0",
        "description": "Line 1
Line 2 with	tab"
    }"""
    mods, findings, _ = parse_fabric_mod_json(content)
    assert len(mods) == 1
    assert mods[0].id == "lenient_mod"
    assert any(f.code == LENIENT_JSON_REPAIRED for f in findings)


def test_fabric_invalid_json():
    content = "{ id: non-json }"
    mods, findings, _ = parse_fabric_mod_json(content)
    assert len(mods) == 0
    assert any(f.code == INVALID_METADATA_JSON for f in findings)
    assert len(findings[0].evidence) >= 1


def test_fabric_unresolved_placeholder():
    content = """{
        "schemaVersion": 1,
        "id": "placeholder_mod",
        "version": "${version}"
    }"""
    mods, findings, _ = parse_fabric_mod_json(content)
    assert len(mods) == 1
    assert mods[0].version is None
    assert any(f.code == UNRESOLVED_PLACEHOLDER for f in findings)


def test_quilt_parser_full():
    content = """{
        "schema_version": 1,
        "quilt_loader": {
            "id": "test_quilt",
            "version": "2.0.0",
            "metadata": {
                "name": "Quilty",
                "description": "A quilt mod",
                "contributors": {
                    "QuiltDev": "Owner",
                    "Artist": "Art"
                },
                "license": "Apache-2.0"
            },
            "provides": ["quilty_api", {"id": "quilty_extra", "version": "1.0"}],
            "depends": [
                "simple_dep",
                {"id": "qdep", "versions": ">=1.0.0"},
                {"id": "qopt", "optional": true},
                {"id": "qcomplex", "versions": {"any": [">=1.0", "<2.0"]}}
            ],
            "breaks": [
                {"id": "qbad", "versions": "*"}
            ],
            "jars": ["META-INF/jars/quilt_sub.jar"]
        }
    }"""
    mods, findings, nested = parse_quilt_mod_json(content)
    assert len(mods) == 1
    m = mods[0]
    assert m.id == "test_quilt"
    assert m.name == "Quilty"
    assert m.loaders == [Loader.QUILT]
    assert m.license == "Apache-2.0"
    assert "quilty_api" in m.provides
    assert "quilty_extra" in m.provides
    assert "QuiltDev" in m.authors
    assert "Artist" in m.authors
    assert "quilt_sub.jar" in nested[0]

    deps_by_id = {d.mod_id: d for d in m.dependencies}
    assert "simple_dep" in deps_by_id
    assert deps_by_id["simple_dep"].version_range is None
    assert deps_by_id["simple_dep"].kind == DependencyKind.REQUIRED
    assert deps_by_id["simple_dep"].declared_as == "depends"

    assert deps_by_id["qdep"].version_range is not None
    assert deps_by_id["qdep"].version_range.raw == ">=1.0.0"

    assert deps_by_id["qopt"].version_range is None
    assert deps_by_id["qopt"].kind == DependencyKind.OPTIONAL
    assert deps_by_id["qopt"].declared_as == "depends"

    assert "qbad" in deps_by_id
    assert deps_by_id["qbad"].kind == DependencyKind.BREAKS
    assert deps_by_id["qbad"].declared_as == "breaks"
    assert deps_by_id["qbad"].version_range is not None
    assert deps_by_id["qbad"].version_range.raw == "*"

    # Complex object kept and flagged
    assert "qcomplex" in deps_by_id
    assert deps_by_id["qcomplex"].version_range is not None
    assert deps_by_id["qcomplex"].version_range.is_valid is False
    assert any(f.code == INVALID_VERSION_RANGE for f in findings)


def test_forge_toml_multi_mod_and_placeholder_resolution():
    content = """
    modLoader = "javafml"
    loaderVersion = "[47,)"
    license = "LGPL-3.0"

    [[mods]]
    modId = "first_mod"
    version = "${file.jarVersion}"
    displayName = "First Mod"
    authors = "Author A, Author B"

    [[dependencies.first_mod]]
    modId = "minecraft"
    mandatory = true
    versionRange = "[1.20.1]"

    [[dependencies.first_mod]]
    modId = "optional_forge_mod"
    mandatory = false
    versionRange = "[1.0,)"

    [[mods]]
    modId = "second_mod"
    version = "1.2.3"
    displayName = "Second Mod"
    """
    # Test with manifest version resolved
    mods, findings = parse_forge_toml(
        content,
        file_path="META-INF/mods.toml",
        manifest_version="47.1.0",
    )
    assert len(mods) == 2
    assert mods[0].id == "first_mod"
    assert mods[0].version == "47.1.0"
    assert mods[0].loaders == [Loader.FORGE]
    assert len(mods[0].authors) == 2

    deps = {d.mod_id: d for d in mods[0].dependencies}
    assert deps["minecraft"].kind == DependencyKind.REQUIRED
    assert deps["optional_forge_mod"].kind == DependencyKind.OPTIONAL

    assert mods[1].id == "second_mod"
    assert mods[1].version == "1.2.3"


def test_forge_unresolved_placeholder():
    content = """
    [[mods]]
    modId = "unresolved_mod"
    version = "${file.jarVersion}"
    """
    # No manifest version passed
    mods, findings = parse_forge_toml(content, file_path="META-INF/mods.toml", manifest_version=None)
    assert len(mods) == 1
    assert mods[0].version is None
    assert any(f.code == UNRESOLVED_PLACEHOLDER for f in findings)


def test_neoforge_toml_dependency_types():
    content = """
    [[mods]]
    modId = "test_neoforge"
    version = "1.0.0"

    [[dependencies.test_neoforge]]
    modId = "dep_req"
    type = "required"

    [[dependencies.test_neoforge]]
    modId = "dep_opt"
    type = "optional"

    [[dependencies.test_neoforge]]
    modId = "dep_incompat"
    type = "incompatible"

    [[dependencies.test_neoforge]]
    modId = "dep_discouraged"
    type = "discouraged"
    """
    mods, findings = parse_forge_toml(content, file_path="META-INF/neoforge.mods.toml")
    assert len(mods) == 1
    m = mods[0]
    assert m.loaders == [Loader.NEOFORGE]

    dep_kinds = {d.mod_id: d.kind for d in m.dependencies}
    assert dep_kinds["dep_req"] == DependencyKind.REQUIRED
    assert dep_kinds["dep_opt"] == DependencyKind.OPTIONAL
    assert dep_kinds["dep_incompat"] == DependencyKind.BREAKS
    assert dep_kinds["dep_discouraged"] == DependencyKind.CONFLICTS


def test_forge_invalid_toml():
    content = "not a toml file = = = "
    mods, findings = parse_forge_toml(content, file_path="META-INF/mods.toml")
    assert len(mods) == 0
    assert any(f.code == INVALID_METADATA_TOML for f in findings)


def test_forge_dep_without_version_range():
    content = """
    [[mods]]
    modId = "simple_forge"
    version = "1.0.0"

    [[dependencies.simple_forge]]
    modId = "no_range_dep"
    mandatory = true
    """
    mods, findings = parse_forge_toml(content, file_path="META-INF/mods.toml")
    assert len(mods) == 1
    dep = mods[0].dependencies[0]
    assert dep.mod_id == "no_range_dep"
    assert dep.version_range is None

