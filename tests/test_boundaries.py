"""Boundary verification tests ensuring architectural layering constraints."""

import ast
from pathlib import Path


def _get_imports(file_path: Path) -> list[str]:
    """Parse python file and extract all imported module names."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def test_no_pyside6_outside_ui():
    """No file outside src/mmis/ui should import PySide6."""
    src_root = Path(__file__).resolve().parent.parent / "src" / "mmis"
    for py_file in src_root.rglob("*.py"):
        if "ui" in py_file.parts:
            continue
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("PySide6"), (
                f"Forbidden import '{imp}' found in {py_file.relative_to(src_root)}"
            )


def test_ui_imports_only_services_and_domain():
    """src/mmis/ui imports only mmis.services and mmis.domain (never storage, minecraft or analysis directly)."""
    src_root = Path(__file__).resolve().parent.parent / "src" / "mmis"
    ui_root = src_root / "ui"
    if not ui_root.exists():
        return

    for py_file in ui_root.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("mmis.storage"), (
                f"Forbidden storage import '{imp}' found in {py_file.relative_to(src_root)}"
            )
            assert not imp.startswith("mmis.minecraft"), (
                f"Forbidden minecraft import '{imp}' found in {py_file.relative_to(src_root)}"
            )
            assert not imp.startswith("mmis.analysis"), (
                f"Forbidden analysis import '{imp}' found in {py_file.relative_to(src_root)}"
            )


def test_import_mmis_cli_does_not_load_pyside6():
    """import mmis.cli must leave PySide6 unloaded."""
    import subprocess
    import sys

    code = "import sys; import mmis.cli; sys.exit(1 if any(m.startswith('PySide6') for m in sys.modules) else 0)"
    res = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert res.returncode == 0, "import mmis.cli loaded PySide6 into sys.modules"


def test_domain_has_no_minecraft_imports():
    """Domain layer must not import from minecraft, storage, analysis, services, or cli."""
    domain_root = Path(__file__).resolve().parent.parent / "src" / "mmis" / "domain"
    for py_file in domain_root.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("mmis.minecraft"), f"Forbidden import '{imp}' in domain file {py_file.name}"
            assert not imp.startswith("mmis.storage"), f"Forbidden import '{imp}' in domain file {py_file.name}"
            assert not imp.startswith("mmis.analysis"), f"Forbidden import '{imp}' in domain file {py_file.name}"
            assert not imp.startswith("mmis.services"), f"Forbidden import '{imp}' in domain file {py_file.name}"
            assert not imp.startswith("mmis.cli"), f"Forbidden import '{imp}' in domain file {py_file.name}"


def test_storage_imports_only_domain():
    """Storage layer must import only domain (no minecraft, analysis, services, or cli)."""
    storage_root = Path(__file__).resolve().parent.parent / "src" / "mmis" / "storage"
    for py_file in storage_root.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("mmis.minecraft"), f"Forbidden import '{imp}' in storage file {py_file.name}"
            assert not imp.startswith("mmis.analysis"), f"Forbidden import '{imp}' in storage file {py_file.name}"
            assert not imp.startswith("mmis.services"), f"Forbidden import '{imp}' in storage file {py_file.name}"
            assert not imp.startswith("mmis.cli"), f"Forbidden import '{imp}' in storage file {py_file.name}"


def test_analysis_imports_only_domain():
    """Analysis layer must import only domain (no minecraft, storage, services, or cli)."""
    analysis_root = Path(__file__).resolve().parent.parent / "src" / "mmis" / "analysis"
    for py_file in analysis_root.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("mmis.minecraft"), f"Forbidden import '{imp}' in analysis file {py_file.name}"
            assert not imp.startswith("mmis.storage"), f"Forbidden import '{imp}' in analysis file {py_file.name}"
            assert not imp.startswith("mmis.services"), f"Forbidden import '{imp}' in analysis file {py_file.name}"
            assert not imp.startswith("mmis.cli"), f"Forbidden import '{imp}' in analysis file {py_file.name}"


def test_crash_modules_architectural_boundaries():
    """Crash domain and analysis modules adhere to strict architectural isolation."""
    src_root = Path(__file__).resolve().parent.parent / "src" / "mmis"
    crash_domain = src_root / "domain" / "crash.py"
    crash_analysis = src_root / "analysis" / "crashlog.py"

    assert crash_domain.exists()
    assert crash_analysis.exists()

    domain_imports = _get_imports(crash_domain)
    for imp in domain_imports:
        assert imp.startswith("mmis.domain") or not imp.startswith("mmis."), (
            f"domain/crash.py cannot import outside domain: {imp}"
        )

    analysis_imports = _get_imports(crash_analysis)
    for imp in analysis_imports:
        assert imp.startswith("mmis.domain") or not imp.startswith("mmis."), (
            f"analysis/crashlog.py must only import domain: {imp}"
        )


def test_services_do_not_import_ui():
    """src/mmis/services must never import mmis.ui or PySide6."""
    src_root = Path(__file__).resolve().parent.parent / "src" / "mmis"
    services_root = src_root / "services"
    for py_file in services_root.rglob("*.py"):
        imports = _get_imports(py_file)
        for imp in imports:
            assert not imp.startswith("mmis.ui"), f"Forbidden UI import '{imp}' in {py_file.name}"
            assert not imp.startswith("PySide6"), f"Forbidden PySide6 import '{imp}' in {py_file.name}"
