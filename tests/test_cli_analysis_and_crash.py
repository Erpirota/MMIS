"""CLI end-to-end subprocess tests for analyze and crash subcommands."""

import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from tests.helpers import create_jar


def test_cli_analyze_human_and_json(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": json.dumps({
            "schemaVersion": 1,
            "id": "mod_a",
            "version": "1.0.0",
            "depends": {"mod_b": ">=1.0.0"}
        })
    })

    # 1. Human output: analyze
    cmd = [sys.executable, "-m", "mmis", "analyze", str(tmp_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "=== MMIS Modpack Analysis Report ===" in proc.stdout
    assert "DEP_MISSING_REQUIRED" in proc.stdout

    # 2. JSON output: analyze --json
    cmd_json = [sys.executable, "-m", "mmis", "analyze", str(tmp_path), "--json"]
    proc_json = subprocess.run(cmd_json, capture_output=True, text=True)
    assert proc_json.returncode == 0
    data = json.loads(proc_json.stdout)
    assert "findings" in data
    assert "severity_counts" in data
    assert any(f["code"] == "DEP_MISSING_REQUIRED" for f in data["findings"])

    # 3. Determinism across seeds
    seeds = ["0", "1", "42", "999"]
    outputs = []
    for s in seeds:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = s
        p = subprocess.run(cmd_json, capture_output=True, text=True, env=env)
        assert p.returncode == 0
        outputs.append(p.stdout)
    for i in range(1, len(outputs)):
        assert outputs[i] == outputs[0]


def test_cli_analyze_exit_codes(tmp_path: Path):
    # Non-existent directory
    proc = subprocess.run([sys.executable, "-m", "mmis", "analyze", str(tmp_path / "missing")], capture_output=True, text=True)
    assert proc.returncode == 2

    # Missing argument
    proc2 = subprocess.run([sys.executable, "-m", "mmis", "analyze"], capture_output=True, text=True)
    assert proc2.returncode == 2


def test_cli_crash_lifecycle(tmp_path: Path):
    data_dir = tmp_path / "mmis_data"
    crash_file = tmp_path / "sample_crash.txt"
    crash_file.write_text(
        "---- Minecraft Crash Report ----\n"
        "Description: Test crash\n"
        "java.lang.NullPointerException: Boom\n"
        "\tat com.example.mod.Tile.tick(Tile.java:10)\n",
        encoding="utf-8",
    )

    # 1. crash analyze
    cmd = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "analyze", str(crash_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "=== MMIS Crash Analysis ===" in proc.stdout
    assert "java.lang.NullPointerException" in proc.stdout

    # 2. crash analyze --json
    cmd_json = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "analyze", str(crash_file), "--json",
    ]
    proc_json = subprocess.run(cmd_json, capture_output=True, text=True)
    assert proc_json.returncode == 0
    data = json.loads(proc_json.stdout)
    assert data["kind"] == "crash_report"

    # 3. crash analyze --save
    cmd_save = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "analyze", str(crash_file), "--save",
    ]
    proc_save = subprocess.run(cmd_save, capture_output=True, text=True)
    assert proc_save.returncode == 0

    # 4. crash list
    cmd_list = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "list",
    ]
    proc_list = subprocess.run(cmd_list, capture_output=True, text=True)
    assert proc_list.returncode == 0
    assert "sample_crash.txt" in proc_list.stdout

    # crash list --json
    cmd_list_json = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "list", "--json",
    ]
    proc_list_json = subprocess.run(cmd_list_json, capture_output=True, text=True)
    assert proc_list_json.returncode == 0
    list_data = json.loads(proc_list_json.stdout)
    assert len(list_data) == 1
    crash_id = list_data[0]["id"]

    # 5. crash show <id>
    cmd_show = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "show", str(crash_id),
    ]
    proc_show = subprocess.run(cmd_show, capture_output=True, text=True)
    assert proc_show.returncode == 0
    assert f"Crash Report ID: {crash_id}" in proc_show.stdout

    # 6. crash delete <id>
    cmd_del = [
        sys.executable, "-m", "mmis", "--data-dir", str(data_dir),
        "crash", "delete", str(crash_id),
    ]
    proc_del = subprocess.run(cmd_del, capture_output=True, text=True)
    assert proc_del.returncode == 0
    assert "deleted" in proc_del.stdout.lower()

    # 7. Non-existent file / not found exit codes
    proc_missing_file = subprocess.run(
        [sys.executable, "-m", "mmis", "--data-dir", str(data_dir), "crash", "analyze", str(tmp_path / "nope.txt")],
        capture_output=True, text=True
    )
    assert proc_missing_file.returncode == 2

    proc_missing_id = subprocess.run(
        [sys.executable, "-m", "mmis", "--data-dir", str(data_dir), "crash", "show", "99999"],
        capture_output=True, text=True
    )
    assert proc_missing_id.returncode == 2


def test_cli_crash_analyze_without_save_does_not_create_data_dir(tmp_path: Path):
    """crash analyze without --save must not create the database or data directory."""
    non_existing_dir = tmp_path / "never_created_data_dir"
    assert not non_existing_dir.exists()

    crash_file = tmp_path / "test_crash.txt"
    crash_file.write_text("java.lang.RuntimeException: test error\n\tat com.example.MyClass.test(MyClass.java:10)", encoding="utf-8")

    env = dict(os.environ)
    env["MMIS_DATA_DIR"] = str(non_existing_dir)

    cmd = [sys.executable, "-m", "mmis", "crash", "analyze", str(crash_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)

    assert proc.returncode == 0
    assert not non_existing_dir.exists()


def test_cli_help_at_all_levels(tmp_path: Path):
    """--help and -h at every level must print help and exit 0 without creating data dir."""
    non_existing_dir = tmp_path / "help_data_dir"
    env = dict(os.environ)
    env["MMIS_DATA_DIR"] = str(non_existing_dir)

    help_invocations = [
        [],  # --help at top level
        ["scan"],
        ["analyze"],
        ["profile"],
        ["profile", "add"],
        ["profile", "list"],
        ["profile", "scan"],
        ["profile", "history"],
        ["profile", "diff"],
        ["crash"],
        ["crash", "analyze"],
        ["crash", "list"],
        ["crash", "show"],
        ["crash", "delete"],
    ]

    for inv in help_invocations:
        for flag in ["--help", "-h"]:
            cmd = [sys.executable, "-m", "mmis"] + inv + [flag]
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            assert proc.returncode == 0, f"Failed on '{' '.join(cmd)}': exit code {proc.returncode}, stderr: {proc.stderr}"
            assert "usage:" in proc.stdout.lower() or "help" in proc.stdout.lower()
            assert not non_existing_dir.exists()

