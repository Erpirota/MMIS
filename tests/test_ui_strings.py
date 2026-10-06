"""Static verification that all strings.NAME accesses in UI modules exist in strings.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import mmis.ui.strings as strings_mod


def _get_strings_attributes() -> set[str]:
    return {name for name in dir(strings_mod) if not name.startswith("_")}


def test_all_ui_strings_attributes_exist():
    defined_attrs = _get_strings_attributes()
    ui_dir = Path(__file__).resolve().parent.parent / "src" / "mmis" / "ui"

    missing: list[str] = []

    for py_file in ui_dir.rglob("*.py"):
        if py_file.name == "strings.py":
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        # Check if file imports strings from mmis.ui
        has_strings_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module in ("mmis.ui", "..", ".") and any(alias.name == "strings" for alias in node.names):
                    has_strings_import = True
            elif isinstance(node, ast.Import):
                if any(alias.name == "mmis.ui.strings" for alias in node.names):
                    has_strings_import = True

        if not has_strings_import:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "strings":
                    attr_name = node.attr
                    if attr_name not in defined_attrs:
                        missing.append(f"{py_file.name}:{node.lineno}: strings.{attr_name}")

    assert not missing, f"Missing strings attributes found:\n" + "\n".join(missing)
