"""Seed deterministic synthetic demo data for MMIS GUI and screenshots."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import zipfile

from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.storage.repository import ProfileRepository


def _create_jar(
    jar_path: Path,
    files: dict[str, str | bytes],
) -> None:
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(jar_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in files.items():
            if isinstance(content, str):
                zf.writestr(filename, content.encode("utf-8"))
            else:
                zf.writestr(filename, content)


def generate_synthetic_pack(pack_dir: Path) -> None:
    """Generate 40+ realistic jar files with various health, conflict, and dependency scenarios."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    # 1. Base healthy Fabric mods (25 mods)
    healthy_mods = [
        ("fabric-api", "Fabric API", "0.92.0", ["fabric"]),
        ("sodium", "Sodium", "0.5.8", ["fabric"]),
        ("iris", "Iris Shaders", "1.6.17", ["fabric"]),
        ("lithium", "Lithium", "0.11.2", ["fabric"]),
        ("ferritecore", "FerriteCore", "6.0.1", ["fabric"]),
        ("entityculling", "Entity Culling", "1.6.2", ["fabric"]),
        ("cloth-config", "Cloth Config v13", "13.0.121", ["fabric"]),
        ("modmenu", "Mod Menu", "9.0.0", ["fabric"]),
        ("appleskin", "AppleSkin", "2.5.1", ["fabric"]),
        ("roughlyenoughitems", "Roughly Enough Items (REI)", "12.0.684", ["fabric"]),
        ("jei", "Just Enough Items", "15.3.0.4", ["fabric", "forge"]),
        ("wthit", "WTHIT", "8.7.0", ["fabric"]),
        ("indium", "Indium", "1.0.30", ["fabric"]),
        ("continuity", "Continuity", "3.0.0-beta.4", ["fabric"]),
        ("citresewn", "CIT Resewn", "1.1.4", ["fabric"]),
        ("zoomify", "Zoomify", "2.11.2", ["fabric"]),
        ("architectury", "Architectury API", "9.2.14", ["fabric"]),
        ("cloth-basic-math", "Cloth Basic Math", "0.6.1", ["fabric"]),
        ("criterialib", "Criteria Lib", "1.1.0", ["fabric"]),
        ("sound-physics-remastered", "Sound Physics Remastered", "1.4.3", ["fabric"]),
        ("fallingleaves", "Falling Leaves", "1.15.4", ["fabric"]),
        ("ambient-sounds", "AmbientSounds", "5.2.20", ["fabric"]),
        ("xaeros-minimap", "Xaero's Minimap", "23.9.7", ["fabric"]),
        ("xaeros-world-map", "Xaero's World Map", "1.37.8", ["fabric"]),
        ("dynamic-fps", "Dynamic FPS", "3.4.3", ["fabric"]),
    ]

    for mod_id, name, version, loaders in healthy_mods:
        f_meta = {
            "schemaVersion": 1,
            "id": mod_id,
            "version": version,
            "name": name,
            "description": f"Standard synthetic mod {name} for modpack testing.",
            "authors": ["TestAuthor"],
            "license": "MIT",
            "environment": "*",
            "depends": {"fabricloader": ">=0.15.0", "minecraft": "~1.20.1"},
        }
        _create_jar(mods_dir / f"{mod_id}-{version}.jar", {
            "fabric.mod.json": json.dumps(f_meta),
        })

    # 1. Pack manifest (CurseForge format: Fabric 1.20.1)
    manifest_data = {
        "minecraft": {
            "version": "1.20.1",
            "modLoaders": [
                {
                    "id": "fabric-0.15.11",
                    "primary": True,
                }
            ],
        },
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "Demo Modpack",
        "version": "1.0.0",
        "author": "MMIS",
        "files": [],
    }
    with open(pack_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # 2. Exactly one stray Forge-only mod (demonstrates loader mismatch finding)
    stray_forge_toml = """
    [[mods]]
    modId = "stray_forge_utility"
    version = "1.0.0"
    displayName = "Stray Forge Utility"
    description = "Stray Forge-only utility mod installed in a Fabric modpack"
    authors = "ForgeAuthor"
    """
    _create_jar(mods_dir / "stray_forge_utility-1.0.0.jar", {
        "META-INF/mods.toml": stray_forge_toml,
    })

    # 3. Dual-Loader Jar (emit both fabric.mod.json and META-INF/mods.toml)
    dual_fabric = {
        "schemaVersion": 1,
        "id": "cross_loader_lib",
        "version": "2.0.0",
        "name": "Cross Loader Library (Fabric)",
        "depends": {"fabricloader": ">=0.14.0"},
    }
    dual_forge = """
    [[mods]]
    modId = "cross_loader_lib"
    version = "2.0.0"
    displayName = "Cross Loader Library (Forge)"
    description = "Runs on both Fabric and Forge"
    """
    _create_jar(mods_dir / "cross_loader_lib-2.0.0.jar", {
        "fabric.mod.json": json.dumps(dual_fabric),
        "META-INF/mods.toml": dual_forge,
    })

    # 4. Mod with Missing Dependency
    missing_dep_meta = {
        "schemaVersion": 1,
        "id": "mod_broken_deps",
        "version": "1.0.0",
        "name": "Broken Dependencies Mod",
        "depends": {
            "nonexistent_magic_library": ">=3.0.0",
            "missing_core_framework": "*",
        },
    }
    _create_jar(mods_dir / "mod_broken_deps-1.0.0.jar", {
        "fabric.mod.json": json.dumps(missing_dep_meta),
    })

    # 5. Mod with Minecraft Version Incompatibility
    incompat_meta = {
        "schemaVersion": 1,
        "id": "future_mod_incompat",
        "version": "1.0.0",
        "name": "Future MC Version Mod",
        "depends": {
            "minecraft": ">=1.21.0",
        },
    }
    _create_jar(mods_dir / "future_mod_incompat-1.0.0.jar", {
        "fabric.mod.json": json.dumps(incompat_meta),
    })

    # 6. Duplicate Mod IDs in separate jars
    dup_a = {
        "schemaVersion": 1,
        "id": "duplicate_conflict_mod",
        "version": "1.0.0",
        "name": "Duplicate Conflict Mod A",
    }
    dup_b = {
        "schemaVersion": 1,
        "id": "duplicate_conflict_mod",
        "version": "1.0.1",
        "name": "Duplicate Conflict Mod B",
    }
    _create_jar(mods_dir / "duplicate_mod-1.0.0.jar", {"fabric.mod.json": json.dumps(dup_a)})
    _create_jar(mods_dir / "duplicate_mod-1.0.1.jar", {"fabric.mod.json": json.dumps(dup_b)})

    # 7. Mod with Declared Breaks
    breaks_meta = {
        "schemaVersion": 1,
        "id": "antagonist_mod",
        "version": "1.0.0",
        "name": "Antagonist Mod",
        "breaks": {
            "duplicate_conflict_mod": "*",
        },
    }
    _create_jar(mods_dir / "antagonist_mod-1.0.0.jar", {"fabric.mod.json": json.dumps(breaks_meta)})

    # 8. Disabled Jar file (.jar.disabled)
    disabled_meta = {
        "schemaVersion": 1,
        "id": "temporarily_disabled_mod",
        "version": "0.9.0",
        "name": "Temporarily Disabled Mod",
    }
    _create_jar(mods_dir / "temporarily_disabled_mod-0.9.0.jar.disabled", {
        "fabric.mod.json": json.dumps(disabled_meta),
    })

    # 9. Nested Jar inside parent jar
    nested_inner_meta = {
        "schemaVersion": 1,
        "id": "nested_inner_utility",
        "version": "1.2.0",
        "name": "Nested Inner Utility",
    }
    inner_jar_bytes = bytearray()
    import io
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(nested_inner_meta).encode("utf-8"))
    inner_jar_bytes = bio.getvalue()

    parent_meta = {
        "schemaVersion": 1,
        "id": "parent_container_mod",
        "version": "1.0.0",
        "name": "Parent Container Mod",
        "jars": [{"file": "META-INF/jars/nested_inner_utility.jar"}],
    }
    _create_jar(mods_dir / "parent_container_mod-1.0.0.jar", {
        "fabric.mod.json": json.dumps(parent_meta),
        "META-INF/jars/nested_inner_utility.jar": inner_jar_bytes,
    })

    # 10. Jar without metadata
    _create_jar(mods_dir / "empty_asset_library-1.0.0.jar", {
        "assets/empty/icon.png": b"fake_png_data",
    })

    # 11. Unknown Version / Missing fields
    unknown_meta = {
        "schemaVersion": 1,
        "id": "mysterious_mod",
        # No version, no description, no name
    }
    _create_jar(mods_dir / "mysterious_mod.jar", {
        "fabric.mod.json": json.dumps(unknown_meta),
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic modpack data for MMIS testing and demo.")
    parser.add_argument("--data-dir", required=True, help="MMIS application data directory")
    parser.add_argument("--pack-dir", required=True, help="Synthetic modpack target directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    pack_dir = Path(args.pack_dir).resolve()

    data_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate synthetic pack
    generate_synthetic_pack(pack_dir)

    # 2. Setup database and ProfileService
    db_path = get_db_path(data_dir)
    repo = ProfileRepository(db_path)
    service = ProfileService(repo)

    # 3. Create Profile 1: Primary Demo Pack
    p1 = service.add_profile("Demo Modpack", pack_dir)

    # Initial Scan 1 (labeled and pinned)
    scan1 = service.scan_profile(p1.name, label="Initial Import")
    service.set_scan_pinned(p1.name, scan1.summary.id, True)

    # Changes for Scan 2:
    # 1. Mod added
    extra_mod = {
        "schemaVersion": 1,
        "id": "quick_hotfix_mod",
        "version": "1.0.1",
        "name": "Quick Hotfix Mod",
    }
    _create_jar(pack_dir / "mods" / "quick_hotfix_mod-1.0.1.jar", {
        "fabric.mod.json": json.dumps(extra_mod),
    })

    # 2. Mod removed
    zoomify_jar = pack_dir / "mods" / "zoomify-2.11.2.jar"
    if zoomify_jar.exists():
        zoomify_jar.unlink()

    # 3. Version change
    appleskin_old = pack_dir / "mods" / "appleskin-2.5.1.jar"
    if appleskin_old.exists():
        appleskin_old.unlink()
    appleskin_new_meta = {
        "schemaVersion": 1,
        "id": "appleskin",
        "version": "2.5.2",
        "name": "AppleSkin",
        "description": "Standard synthetic mod AppleSkin for modpack testing.",
        "authors": ["TestAuthor"],
        "license": "MIT",
        "environment": "*",
        "depends": {"fabricloader": ">=0.15.0", "minecraft": "~1.20.1"},
    }
    _create_jar(pack_dir / "mods" / "appleskin-2.5.2.jar", {
        "fabric.mod.json": json.dumps(appleskin_new_meta),
    })

    # 4. Jar disabled
    sound_jar = pack_dir / "mods" / "sound-physics-remastered-1.4.3.jar"
    if sound_jar.exists():
        sound_jar.rename(pack_dir / "mods" / "sound-physics-remastered-1.4.3.jar.disabled")

    # 5. Finding resolved: replace stray Forge mod with valid Fabric mod
    forge_util = pack_dir / "mods" / "stray_forge_utility-1.0.0.jar"
    if forge_util.exists():
        forge_util.unlink()
    fixed_util_meta = {
        "schemaVersion": 1,
        "id": "stray_forge_utility",
        "version": "1.0.1",
        "name": "Stray Forge Utility (Fabric Port)",
        "description": "Ported to Fabric loader, resolving incompatibility.",
        "depends": {"fabricloader": ">=0.15.0"},
    }
    _create_jar(pack_dir / "mods" / "stray_forge_utility-1.0.1.jar", {
        "fabric.mod.json": json.dumps(fixed_util_meta),
    })

    # 6. New finding: malformed syntax in a new mod
    _create_jar(pack_dir / "mods" / "malformed_syntax_mod-1.0.0.jar", {
        "fabric.mod.json": "{ invalid_json_syntax: true, ",
    })

    scan2 = service.scan_profile(p1.name, label="After Changes")

    # 4. Create Profile 2: Clean Modpack (0 issues)
    clean_dir = pack_dir.parent / "clean_modpack"
    clean_mods_dir = clean_dir / "mods"
    clean_mods_dir.mkdir(parents=True, exist_ok=True)
    clean_mods = [
        ("clean_mod_alpha", "Clean Mod Alpha", "1.0.0"),
        ("clean_mod_beta", "Clean Mod Beta", "1.0.0"),
        ("clean_mod_gamma", "Clean Mod Gamma", "1.0.0"),
    ]
    for cid, cname, cver in clean_mods:
        cmeta = {
            "schemaVersion": 1,
            "id": cid,
            "version": cver,
            "name": cname,
            "description": f"Clean valid mod {cname}.",
            "authors": ["CleanAuthor"],
            "license": "MIT",
            "environment": "*",
        }
        _create_jar(clean_mods_dir / f"{cid}-{cver}.jar", {
            "fabric.mod.json": json.dumps(cmeta),
        })
    p_clean = service.add_profile("Clean Modpack", clean_dir)
    service.scan_profile(p_clean.name, label="v1.0 Clean")

    # 5. Create Profile 3: Never Scanned Pack
    never_scanned_dir = pack_dir.parent / "empty_never_scanned"
    never_scanned_dir.mkdir(parents=True, exist_ok=True)
    service.add_profile("Never Scanned Pack", never_scanned_dir)

    # 6. Create Profile 4: Missing Directory Pack
    missing_dir = pack_dir.parent / "deleted_or_missing_path"
    if missing_dir.exists():
        shutil.rmtree(missing_dir)
    service.add_profile("Missing Path Pack", missing_dir)

    # 7. Seed demo crash reports
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "crash"
    c1_path = fixtures_dir / "fabric_loader_resolution_error.txt"
    if c1_path.exists():
        service.import_crash(c1_path, profile_name=p1.name)

    c2_path = fixtures_dir / "plain_text_no_exception.txt"
    if c2_path.exists():
        service.import_crash(c2_path, profile_name=p1.name)

    c3_path = fixtures_dir / "fabric_crash.txt"
    if c3_path.exists():
        service.import_crash(c3_path, profile_name=None)

    print("Successfully seeded demo modpack and profiles:")
    print(f"  Data dir: {data_dir}")
    print(f"  Pack dir: {pack_dir} ({len(list((pack_dir / 'mods').glob('*.jar*')))} jars)")
    print(f"  Profiles created: Demo Modpack (2 scans, 1 pinned), Clean Modpack (0 issues), Never Scanned Pack, Missing Path Pack")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
