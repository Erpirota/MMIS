"""Build script to create standalone Windows distribution bundle for MMIS using PyInstaller."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def get_dir_size_bytes(path: Path) -> int:
    """Calculate recursive size of a directory in bytes."""
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size string."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    spec_path = repo_root / "packaging" / "mmis.spec"
    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        str(spec_path),
    ]

    print(f"Executing: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(repo_root))
    if result.returncode != 0:
        print(f"Error: PyInstaller build failed with return code {result.returncode}", file=sys.stderr)
        return result.returncode

    bundle_dir = dist_dir / "MMIS"
    gui_exe = bundle_dir / "MMIS.exe"
    cli_exe = bundle_dir / "mmis-cli.exe"

    if not gui_exe.is_file():
        print(f"Error: GUI executable missing at {gui_exe}", file=sys.stderr)
        return 1

    if not cli_exe.is_file():
        print(f"Error: CLI executable missing at {cli_exe}", file=sys.stderr)
        return 1

    bundle_size = get_dir_size_bytes(bundle_dir)
    print("\n==========================================")
    print("MMIS Windows Distribution Build Successful")
    print("==========================================")
    print(f"GUI Executable:    {gui_exe}")
    print(f"CLI Executable:    {cli_exe}")
    print(f"Bundle Directory:  {bundle_dir}")
    print(f"Total Bundle Size: {format_size(bundle_size)} ({bundle_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
