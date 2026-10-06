"""End-to-end CLI tests for modpack profile commands and persistence."""

import json
import os
from pathlib import Path
import subprocess
import sys

from tests.helpers import create_jar


def run_cli(*args: str, data_dir: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    """Execute MMIS CLI command via subprocess."""
    cmd = [sys.executable, "-m", "mmis"]
    if data_dir is not None:
        cmd.extend(["--data-dir", str(data_dir)])
    cmd.extend(args)

    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    return subprocess.run(cmd, capture_output=True, text=True, env=run_env)


def test_cli_profile_lifecycle(tmp_path: Path):
    """Test profile add, list, rename, remove end-to-end."""
    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack1"
    pack_dir.mkdir()

    # Add profile
    proc_add = run_cli("profile", "add", "TestPack", str(pack_dir), data_dir=data_dir)
    assert proc_add.returncode == 0
    assert "TestPack" in proc_add.stdout

    # List profiles (human)
    proc_list = run_cli("profile", "list", data_dir=data_dir)
    assert proc_list.returncode == 0
    assert "TestPack" in proc_list.stdout
    assert "never_scanned" in proc_list.stdout

    # List profiles (json)
    proc_list_json = run_cli("profile", "list", "--json", data_dir=data_dir)
    assert proc_list_json.returncode == 0
    data = json.loads(proc_list_json.stdout)
    assert len(data) == 1
    assert data[0]["name"] == "TestPack"
    assert data[0]["status"] == "never_scanned"

    # Rename profile
    proc_rename = run_cli("profile", "rename", "TestPack", "RenamedPack", data_dir=data_dir)
    assert proc_rename.returncode == 0
    assert "RenamedPack" in proc_rename.stdout

    # Remove profile
    proc_remove = run_cli("profile", "remove", "RenamedPack", data_dir=data_dir)
    assert proc_remove.returncode == 0
    assert "removed" in proc_remove.stdout.lower()

    # Verify empty list
    proc_list_empty = run_cli("profile", "list", data_dir=data_dir)
    assert proc_list_empty.returncode == 0
    assert "No profiles configured." in proc_list_empty.stdout


def test_cli_scan_history_diff_pin(tmp_path: Path):
    """Test profile scan, history, diff, and pin/unpin end-to-end."""
    data_dir = tmp_path / "data"
    pack_dir = tmp_path / "pack_game"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir()

    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a", "version": "1.0.0"}'
    })

    # Add profile
    run_cli("profile", "add", "GamePack", str(pack_dir), data_dir=data_dir)

    # First scan
    proc_scan1 = run_cli("profile", "scan", "GamePack", "--label", "V1", data_dir=data_dir)
    assert proc_scan1.returncode == 0
    assert "Scan ID" in proc_scan1.stdout
    assert "Label             : V1" in proc_scan1.stdout

    # Second scan with new mod added
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod_b", "version": "2.0.0"}'
    })
    proc_scan2 = run_cli("profile", "scan", "GamePack", "--label", "V2", "--json", data_dir=data_dir)
    assert proc_scan2.returncode == 0
    scan2_data = json.loads(proc_scan2.stdout)
    assert scan2_data["mod_count"] == 2

    # History
    proc_hist = run_cli("profile", "history", "GamePack", data_dir=data_dir)
    assert proc_hist.returncode == 0
    assert "V1" in proc_hist.stdout
    assert "V2" in proc_hist.stdout

    proc_hist_json = run_cli("profile", "history", "GamePack", "--json", data_dir=data_dir)
    assert proc_hist_json.returncode == 0
    history = json.loads(proc_hist_json.stdout)
    assert len(history) == 2
    # Newest first
    scan_b_id = history[0]["id"]
    scan_a_id = history[1]["id"]

    # Diff default (previous vs latest)
    proc_diff = run_cli("profile", "diff", "GamePack", data_dir=data_dir)
    assert proc_diff.returncode == 0
    assert "Top-Level Mod Changes (1):" in proc_diff.stdout
    assert "mod_b" in proc_diff.stdout

    # Diff explicit IDs
    proc_diff_json = run_cli(
        "profile", "diff", "GamePack", str(scan_a_id), str(scan_b_id), "--json", data_dir=data_dir
    )
    assert proc_diff_json.returncode == 0
    diff_data = json.loads(proc_diff_json.stdout)
    assert diff_data["has_changes"] is True
    assert len(diff_data["top_level_mods"]) == 1
    assert diff_data["top_level_mods"][0]["id"] == "mod_b"
    assert diff_data["top_level_mods"][0]["change_type"] == "added"

    # Pin / Unpin
    proc_pin = run_cli("profile", "pin", "GamePack", str(scan_a_id), data_dir=data_dir)
    assert proc_pin.returncode == 0
    assert f"Pinned scan {scan_a_id}" in proc_pin.stdout

    proc_unpin = run_cli("profile", "unpin", "GamePack", str(scan_a_id), data_dir=data_dir)
    assert proc_unpin.returncode == 0
    assert f"Unpinned scan {scan_a_id}" in proc_unpin.stdout


def test_cli_persistence_across_processes(tmp_path: Path):
    """A8: Profile added and scanned in one process is visible in another process."""
    data_dir = tmp_path / "cross_proc_data"
    pack_dir = tmp_path / "proc_pack"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    create_jar(mods_dir / "test.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "test_cross", "version": "1.0.0"}'
    })

    # Process 1: Add and scan
    p1 = run_cli("profile", "add", "ProcPack", str(pack_dir), data_dir=data_dir)
    assert p1.returncode == 0
    p2 = run_cli("profile", "scan", "ProcPack", data_dir=data_dir)
    assert p2.returncode == 0

    # Process 2: Read history and list
    p3 = run_cli("profile", "history", "ProcPack", "--json", data_dir=data_dir)
    assert p3.returncode == 0
    history = json.loads(p3.stdout)
    assert len(history) == 1
    assert history[0]["loader"] == "fabric"

    p4 = run_cli("profile", "list", "--json", data_dir=data_dir)
    assert p4.returncode == 0
    profiles = json.loads(p4.stdout)
    assert profiles[0]["status"] == "ok"


def test_cli_deterministic_json_hashseed(tmp_path: Path):
    """A7: CLI commands produce byte-identical JSON output across different PYTHONHASHSEEDs."""
    data_dir = tmp_path / "hash_data"
    pack_dir = tmp_path / "hash_pack"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    create_jar(mods_dir / "z_mod.jar", {"fabric.mod.json": '{"schemaVersion": 1, "id": "z_mod"}'})
    create_jar(mods_dir / "a_mod.jar", {"fabric.mod.json": '{"schemaVersion": 1, "id": "a_mod"}'})

    run_cli("profile", "add", "HashPack", str(pack_dir), data_dir=data_dir)
    run_cli("profile", "scan", "HashPack", data_dir=data_dir)

    seeds = ["0", "1", "42", "123", "999"]
    outputs: list[str] = []

    for seed in seeds:
        proc = run_cli(
            "profile",
            "history",
            "HashPack",
            "--json",
            data_dir=data_dir,
            env={"PYTHONHASHSEED": seed},
        )
        assert proc.returncode == 0
        outputs.append(proc.stdout)

    for i in range(1, len(outputs)):
        assert outputs[i] == outputs[0], f"Output differed with PYTHONHASHSEED={seeds[i]}"


def test_cli_exit_codes_and_error_handling(tmp_path: Path):
    """Test exit codes: 0 ok, 2 usage/not found, 1 operational failure."""
    data_dir = tmp_path / "exit_data"
    pack_dir = tmp_path / "valid_pack"
    pack_dir.mkdir()

    run_cli("profile", "add", "ExitPack", str(pack_dir), data_dir=data_dir)

    # 1. Unknown profile -> exit 2
    proc_not_found = run_cli("profile", "scan", "UnknownProfile", data_dir=data_dir)
    assert proc_not_found.returncode == 2
    assert "not found" in proc_not_found.stderr.lower()

    # 2. Unknown scan ID -> exit 2
    proc_bad_scan = run_cli("profile", "pin", "ExitPack", "9999", data_dir=data_dir)
    assert proc_bad_scan.returncode == 2

    # 3. Invalid diff arguments (1 scan ID instead of 0 or 2) -> exit 2
    proc_bad_diff_args = run_cli("profile", "diff", "ExitPack", "1", data_dir=data_dir)
    assert proc_bad_diff_args.returncode == 2

    # 4. Fewer than 2 scans to diff -> exit 1
    proc_fewer_scans = run_cli("profile", "diff", "ExitPack", data_dir=data_dir)
    assert proc_fewer_scans.returncode == 1
    assert "fewer than two scans" in proc_fewer_scans.stderr.lower()

    # 5. Scanning profile with missing directory -> exit 1
    import shutil
    shutil.rmtree(pack_dir)
    proc_missing_path = run_cli("profile", "scan", "ExitPack", data_dir=data_dir)
    assert proc_missing_path.returncode == 1
    assert "path missing" in proc_missing_path.stderr.lower()


def test_cli_mmis_data_dir_env(tmp_path: Path):
    """CLI honours MMIS_DATA_DIR environment variable."""
    env_dir = tmp_path / "env_data_dir"
    pack_dir = tmp_path / "env_pack"
    pack_dir.mkdir()

    proc = run_cli(
        "profile",
        "add",
        "EnvProfile",
        str(pack_dir),
        env={"MMIS_DATA_DIR": str(env_dir)},
    )
    assert proc.returncode == 0
    assert (env_dir / "mmis.db").exists()
