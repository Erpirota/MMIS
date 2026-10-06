"""CLI tests using subprocess with the venv python interpreter."""

import json
import subprocess
import sys
from pathlib import Path
from tests.helpers import create_jar


def test_cli_human_summary(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "test.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "cli_mod", "version": "1.0.0"}'
    })

    cmd = [sys.executable, "-m", "mmis", "scan", str(tmp_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    assert proc.returncode == 0
    assert "=== MMIS Modpack Scan Summary ===" in proc.stdout
    assert "Detected Loader   : fabric" in proc.stdout
    assert "Total Mods        : 1" in proc.stdout


def test_cli_deterministic_json(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_b.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "b_mod"}'
    })
    create_jar(mods_dir / "mod_a.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "a_mod"}'
    })

    cmd = [sys.executable, "-m", "mmis", "scan", str(tmp_path), "--json"]
    proc1 = subprocess.run(cmd, capture_output=True, text=True)
    proc2 = subprocess.run(cmd, capture_output=True, text=True)

    assert proc1.returncode == 0
    assert proc2.returncode == 0

    # Byte-identical output across two runs
    assert proc1.stdout == proc2.stdout

    # Must be valid json
    data = json.loads(proc1.stdout)
    assert data["detected_loader"] == "fabric"
    assert len(data["mods"]) == 2
    # Mods sorted by id
    assert data["mods"][0]["id"] == "a_mod"
    assert data["mods"][1]["id"] == "b_mod"


def test_cli_deterministic_ambiguous_loader_hashseed(tmp_path: Path):
    """Run CLI with different PYTHONHASHSEED values on an ambiguous loader pack.

    Must produce byte-identical JSON output across all runs.
    """
    import os

    mods_dir = tmp_path / "mods"
    create_jar(mods_dir / "mod_fabric.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "fab_mod"}'
    })
    create_jar(mods_dir / "mod_forge.jar", {
        "META-INF/mods.toml": '[[mods]]\nmodId = "frg_mod"'
    })

    outputs: list[str] = []
    seeds = ["0", "1", "42", "123", "999"]
    for seed in seeds:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-m", "mmis", "scan", str(tmp_path), "--json"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0
        outputs.append(proc.stdout)

    # All outputs must be byte-identical
    for i in range(1, len(outputs)):
        assert outputs[i] == outputs[0], f"Output with PYTHONHASHSEED={seeds[i]} differed from {seeds[0]}"

    data = json.loads(outputs[0])
    assert data["detected_loader"] is None
    finding_codes = [f["code"] for f in data["findings"]]
    assert "AMBIGUOUS_LOADER" in finding_codes


def test_cli_exit_codes(tmp_path: Path):
    # Missing path argument
    proc_no_args = subprocess.run([sys.executable, "-m", "mmis", "scan"], capture_output=True, text=True)
    assert proc_no_args.returncode == 2

    # Non-existent directory
    proc_non_exist = subprocess.run(
        [sys.executable, "-m", "mmis", "scan", str(tmp_path / "non_existent_folder")],
        capture_output=True,
        text=True,
    )
    assert proc_non_exist.returncode == 2

    # Unknown subcommand
    proc_bad_cmd = subprocess.run([sys.executable, "-m", "mmis", "unknown"], capture_output=True, text=True)
    assert proc_bad_cmd.returncode == 2
