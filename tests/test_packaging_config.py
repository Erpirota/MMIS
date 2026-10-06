"""Packaging configuration tests (fast, no build, no Qt).

Validates:
- Spec file and build script exist.
- Spec file contains no absolute paths or usernames.
- Launchers import only stdlib and mmis (via AST inspection).
- All 24 SVG assets are covered by the spec's datas pattern.
- pyproject.toml version matches mmis.__version__.
"""

from __future__ import annotations

import ast
import getpass
import os
from pathlib import Path
import tomllib

import mmis

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_packaging_files_exist():
    """Verify presence of packaging files and build script."""
    spec_path = REPO_ROOT / "packaging" / "mmis.spec"
    gui_launcher = REPO_ROOT / "packaging" / "mmis_gui.py"
    cli_launcher = REPO_ROOT / "packaging" / "mmis_cli.py"
    build_script = REPO_ROOT / "scripts" / "build_package.py"

    assert spec_path.is_file(), f"Missing spec file: {spec_path}"
    assert gui_launcher.is_file(), f"Missing GUI launcher: {gui_launcher}"
    assert cli_launcher.is_file(), f"Missing CLI launcher: {cli_launcher}"
    assert build_script.is_file(), f"Missing build script: {build_script}"


def test_spec_contains_no_absolute_paths_or_username():
    """Verify mmis.spec does not hardcode absolute paths or the current username."""
    spec_text = (REPO_ROOT / "packaging" / "mmis.spec").read_text(encoding="utf-8")

    # Check for hardcoded drive letters or root paths
    assert "C:\\" not in spec_text
    assert "C:/" not in spec_text
    assert "/home/" not in spec_text
    assert "/Users/" not in spec_text

    # Check for usernames
    current_user = getpass.getuser()
    if current_user:
        assert current_user.lower() not in spec_text.lower(), f"Username {current_user} found in spec"


def test_launchers_import_only_mmis():
    """AST check: launchers only import Python standard library or mmis modules."""
    stdlib_allowed = {"sys", "argparse", "os", "pathlib", "typing", "__future__"}

    for launcher_name in ("mmis_gui.py", "mmis_cli.py"):
        launcher_path = REPO_ROOT / "packaging" / launcher_name
        tree = ast.parse(launcher_path.read_text(encoding="utf-8"), filename=str(launcher_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg in stdlib_allowed or root_pkg == "mmis", (
                        f"{launcher_name} imports disallowed module '{alias.name}'"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert root_pkg in stdlib_allowed or root_pkg == "mmis", (
                        f"{launcher_name} imports disallowed module '{node.module}'"
                    )


def test_svg_assets_covered_by_datas_pattern():
    """Verify all 24 SVG assets exist and are covered by datas in mmis.spec."""
    assets_dir = REPO_ROOT / "src" / "mmis" / "ui" / "assets"
    svg_files = list(assets_dir.glob("*.svg"))
    assert len(svg_files) == 24, f"Expected 24 SVG assets, found {len(svg_files)}"

    spec_text = (REPO_ROOT / "packaging" / "mmis.spec").read_text(encoding="utf-8")
    assert "*.svg" in spec_text
    assert "mmis/ui/assets" in spec_text


def test_pyproject_version_matches_package():
    """Verify pyproject.toml version equals mmis.__version__."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]
    assert pyproject_version == mmis.__version__, (
        f"Version mismatch: pyproject.toml ({pyproject_version}) != mmis.__version__ ({mmis.__version__})"
    )
