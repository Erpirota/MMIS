"""End-to-end CLI subprocess tests for M6 Part A (report, backup, export-scan, diff)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


def run_cli(*args: str, data_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "mmis"]
    if data_dir is not None:
        cmd.extend(["--data-dir", str(data_dir)])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.fixture
def cli_env(tmp_path: Path):
    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack"
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    # Add a minimal mod jar
    with zipfile.ZipFile(mods_dir / "testmod-1.0.0.jar", "w") as zf:
        meta = {
            "schemaVersion": 1,
            "id": "testmod",
            "version": "1.0.0",
            "name": "Test Mod",
        }
        zf.writestr("fabric.mod.json", json.dumps(meta))

    # Add profile and scan
    res = run_cli("profile", "add", "TestProfile", str(pack_dir), data_dir=data_dir)
    assert res.returncode == 0
    res = run_cli("profile", "scan", "TestProfile", data_dir=data_dir)
    assert res.returncode == 0

    return data_dir, pack_dir


def test_cli_report_json_and_html_with_force(cli_env: tuple[Path, Path], tmp_path: Path):
    data_dir, _ = cli_env
    json_out = tmp_path / "report.json"
    html_out = tmp_path / "report.html"

    # 1. Report JSON
    res = run_cli("report", "TestProfile", "--format", "json", "--out", str(json_out), data_dir=data_dir)
    assert res.returncode == 0
    assert json_out.is_file()
    report_data = json.loads(json_out.read_text(encoding="utf-8"))
    assert report_data["profile_name"] == "TestProfile"

    # 2. Existing file refusal without --force (exit 1)
    res = run_cli("report", "TestProfile", "--format", "json", "--out", str(json_out), data_dir=data_dir)
    assert res.returncode == 1
    assert "Export target exists" in res.stderr

    # 3. Overwrite with --force
    res = run_cli("report", "TestProfile", "--format", "json", "--out", str(json_out), "--force", data_dir=data_dir)
    assert res.returncode == 0

    # 4. Report HTML
    res = run_cli("report", "TestProfile", "--format", "html", "--out", str(html_out), data_dir=data_dir)
    assert res.returncode == 0
    assert html_out.is_file()
    html_text = html_out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html_text
    assert "Content-Security-Policy" in html_text

    # 5. Non-existent profile (exit 2)
    res = run_cli("report", "NonExistentProfile", "--format", "json", "--out", str(tmp_path / "out.json"), data_dir=data_dir)
    assert res.returncode == 2


def test_cli_backup_create_verify_and_restore(cli_env: tuple[Path, Path], tmp_path: Path):
    data_dir, _ = cli_env
    backup_file = tmp_path / "backup.zip"

    # 1. Backup create
    res = run_cli("backup", "create", "--out", str(backup_file), data_dir=data_dir)
    assert res.returncode == 0
    assert backup_file.is_file()

    # 2. Backup create refusal without --force
    res = run_cli("backup", "create", "--out", str(backup_file), data_dir=data_dir)
    assert res.returncode == 1
    assert "Export target exists" in res.stderr

    # 3. Backup verify
    res = run_cli("backup", "verify", str(backup_file), data_dir=data_dir)
    assert res.returncode == 0
    assert "Backup archive is valid" in res.stdout

    # 4. Backup restore without --yes (prints notice and exits 1)
    res = run_cli("backup", "restore", str(backup_file), data_dir=data_dir)
    assert res.returncode == 1
    assert "--yes" in res.stderr

    # 5. Backup restore with --yes (succeeds)
    res = run_cli("backup", "restore", str(backup_file), "--yes", data_dir=data_dir)
    assert res.returncode == 0
    assert "Restore completed successfully" in res.stdout


def test_cli_export_scan_and_diff_file(cli_env: tuple[Path, Path], tmp_path: Path):
    data_dir, _ = cli_env
    snap_file = tmp_path / "snapshot_1.json"

    # 1. Export scan snapshot
    res = run_cli("profile", "export-scan", "TestProfile", "1", "--out", str(snap_file), data_dir=data_dir)
    assert res.returncode == 0
    assert snap_file.is_file()

    # 2. Refusal without --force
    res = run_cli("profile", "export-scan", "TestProfile", "1", "--out", str(snap_file), data_dir=data_dir)
    assert res.returncode == 1

    # 3. Diff against snapshot file
    res = run_cli("profile", "diff", "TestProfile", "1", "--file", str(snap_file), data_dir=data_dir)
    assert res.returncode == 0
    assert "No differences found" in res.stdout

    # 4. Diff against non-existent file (exit 2)
    res = run_cli("profile", "diff", "TestProfile", "1", "--file", str(tmp_path / "missing.json"), data_dir=data_dir)
    assert res.returncode == 2

    # 5. Diff against invalid snapshot file (exit 1 with Snapshot invalid error)
    bad_snap = tmp_path / "bad.json"
    bad_snap.write_text('{"nope": 1}', encoding="utf-8")
    res = run_cli("profile", "diff", "TestProfile", "1", "--file", str(bad_snap), data_dir=data_dir)
    assert res.returncode == 1
    assert "Snapshot invalid" in res.stderr
