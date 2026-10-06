import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tomllib

import mmis


def test_package_importable():
    assert mmis.__version__


def test_python_version():
    assert sys.version_info >= (3, 14)


def test_stdlib_requirements_available():
    assert tomllib.loads("a = 1") == {"a": 1}
    assert sqlite3.sqlite_version_info >= (3, 35)


def _get_isolated_env() -> dict[str, str]:
    src_dir = str(Path(mmis.__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["PYTHONPATH"] = src_dir
    return env


def test_core_import_does_not_load_qt():
    """Verify non-UI modules do not import PySide6 or shiboken6 in an isolated process."""
    check_code = (
        "import importlib, pkgutil, sys\n"
        "packages = ['mmis.domain', 'mmis.analysis', 'mmis.minecraft', 'mmis.storage', 'mmis.services', 'mmis.cli']\n"
        "for pkg_name in packages:\n"
        "    pkg = importlib.import_module(pkg_name)\n"
        "    if hasattr(pkg, '__path__'):\n"
        "        for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix=f'{pkg_name}.'):\n"
        "            importlib.import_module(modname)\n"
        "qt_modules = [m for m in sys.modules if m.startswith('PySide6') or m.startswith('shiboken6')]\n"
        "if qt_modules:\n"
        "    print(f'Qt modules unexpectedly loaded: {qt_modules}', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check_code],
        env=_get_isolated_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"Non-UI import loaded Qt:\n{result.stderr}"


def test_ui_import_loads_qt():
    """Verify the isolated test harness detects Qt loading when importing mmis.ui.app."""
    check_code = (
        "import sys\n"
        "import mmis.ui.app\n"
        "qt_modules = [m for m in sys.modules if m.startswith('PySide6') or m.startswith('shiboken6')]\n"
        "if not qt_modules:\n"
        "    print('Expected Qt modules to be loaded, but none found', file=sys.stderr)\n"
        "    sys.exit(1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check_code],
        env=_get_isolated_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"UI import test failed:\n{result.stderr}"
