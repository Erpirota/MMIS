"""Tests for ProfileService use cases, status derivation, paths, and concurrency."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import pytest

from mmis.domain.errors import (
    DuplicateProfileError,
    ProfileNotFoundError,
    ProfilePathMissingError,
    ScanNotFoundError,
)
from mmis.domain.models import Loader
from mmis.domain.profiles import ProfileStatus
from mmis.services.paths import get_db_path, resolve_data_dir
from mmis.services.profiles import ProfileService
from mmis.storage.repository import ProfileRepository
from tests.helpers import create_jar


def test_paths_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Data dir resolution honours explicit arg, env var, and default."""
    custom = tmp_path / "custom_data"
    resolved_custom = resolve_data_dir(custom)
    assert resolved_custom == custom
    assert resolved_custom.exists()
    assert get_db_path(custom) == custom / "mmis.db"

    env_dir = tmp_path / "env_data"
    monkeypatch.setenv("MMIS_DATA_DIR", str(env_dir))
    resolved_env = resolve_data_dir(None)
    assert resolved_env == env_dir
    assert resolved_env.exists()

    monkeypatch.delenv("MMIS_DATA_DIR", raising=False)
    fake_appdata = tmp_path / "custom_appdata"
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    fake_home = tmp_path / "custom_home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    default_dir = resolve_data_dir(None)
    assert default_dir.exists()
    if os.name == "nt":
        assert default_dir == fake_appdata / "MMIS"
    else:
        assert default_dir == fake_home / ".local" / "share" / "mmis"


def test_guard_unconfigured_data_dir_stays_inside_temp(tmp_path: Path):
    """Prove the conftest guard: with no explicit data dir, the resolved path is inside the temp area."""
    resolved = resolve_data_dir(None)
    assert str(resolved).startswith(str(tmp_path))
    assert resolved.exists()


def test_guard_default_fallback_stays_inside_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Prove the conftest guard: even when MMIS_DATA_DIR is unset, fallback stays inside temp area."""
    monkeypatch.delenv("MMIS_DATA_DIR", raising=False)
    resolved = resolve_data_dir(None)
    assert str(resolved).startswith(str(tmp_path))
    assert resolved.exists()


def test_service_profile_lifecycle(tmp_path: Path):
    """Add, get, rename, update path, and delete profile with validation."""
    repo = ProfileRepository(tmp_path / "app.db")
    service = ProfileService(repo)

    pack_dir = tmp_path / "my_pack"
    pack_dir.mkdir()

    # Invalid names
    with pytest.raises(ValueError):
        service.add_profile("   ", pack_dir)
    with pytest.raises(ValueError):
        service.add_profile("a" * 101, pack_dir)

    # Add profile
    p = service.add_profile("My Modpack", pack_dir)
    assert p.name == "My Modpack"
    assert p.path == str(pack_dir.resolve())

    # Case-insensitive duplicate check
    with pytest.raises(DuplicateProfileError):
        service.add_profile("my modpack", pack_dir)

    # Get profile
    fetched = service.get_profile("MY MODPACK")
    assert fetched.id == p.id

    with pytest.raises(ProfileNotFoundError):
        service.get_profile("NonExistent")

    # Rename
    service.rename_profile("My Modpack", "Renamed Pack")
    assert service.get_profile("Renamed Pack").name == "Renamed Pack"

    # Update path
    new_dir = tmp_path / "new_pack"
    new_dir.mkdir()
    service.update_profile_path("Renamed Pack", new_dir)
    assert service.get_profile("Renamed Pack").path == str(new_dir.resolve())

    # Remove
    assert service.remove_profile("Renamed Pack") is True
    with pytest.raises(ProfileNotFoundError):
        service.get_profile("Renamed Pack")


def test_scan_roundtrip_equality_and_history(tmp_path: Path):
    """A3: scan_profile round-trips full ScanResult equality and updates history."""
    repo = ProfileRepository(tmp_path / "app.db")
    mock_time = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    service = ProfileService(repo, clock=lambda: mock_time)

    pack_dir = tmp_path / "pack_a"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    create_jar(mods_dir / "test_mod.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "test_mod", "version": "1.0.0"}'
    })

    service.add_profile("Pack A", pack_dir)
    scan_rec = service.scan_profile("Pack A", label="Initial Scan")

    assert scan_rec.summary.loader == "fabric"
    assert scan_rec.summary.mod_count == 1
    assert scan_rec.summary.label == "Initial Scan"
    assert scan_rec.summary.scanned_at == mock_time.isoformat()

    # Round-trip equality check: get_scan must equal fresh scan result
    loaded_res = service.get_scan("Pack A", scan_rec.summary.id)
    assert loaded_res == scan_rec.result
    assert loaded_res.to_dict() == scan_rec.result.to_dict()

    # History ordered newest first
    mock_time2 = datetime(2026, 3, 16, 12, 0, 0, tzinfo=timezone.utc)
    service.clock = lambda: mock_time2
    scan_rec2 = service.scan_profile("Pack A", label="Second Scan")

    history = service.get_history("Pack A")
    assert len(history) == 2
    assert history[0].id == scan_rec2.summary.id
    assert history[1].id == scan_rec.summary.id

    # Foreign profile scan lookup rejection
    pack_b_dir = tmp_path / "pack_b"
    pack_b_dir.mkdir()
    service.add_profile("Pack B", pack_b_dir)
    with pytest.raises(ScanNotFoundError):
        service.get_scan("Pack B", scan_rec.summary.id)


def test_scan_missing_path_raises_and_no_scan_row(tmp_path: Path):
    """Scanning a profile whose path does not exist raises ProfilePathMissingError without creating a row."""
    repo = ProfileRepository(tmp_path / "app.db")
    service = ProfileService(repo)

    missing_dir = tmp_path / "ghost_dir"
    service.add_profile("Ghost", missing_dir)

    with pytest.raises(ProfilePathMissingError):
        service.scan_profile("Ghost")

    assert len(service.get_history("Ghost")) == 0


def test_derived_status_all_five_states(tmp_path: Path):
    """A5: Derived status for never_scanned, ok, warnings, errors, and path_missing."""
    repo = ProfileRepository(tmp_path / "app.db")
    service = ProfileService(repo)

    pack_dir = tmp_path / "status_pack"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir()

    service.add_profile("StatusTest", pack_dir)

    # 1. never_scanned
    summaries = service.list_profiles()
    assert summaries[0].status == ProfileStatus.NEVER_SCANNED

    # 2. ok
    create_jar(mods_dir / "ok.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "ok_mod", "version": "1.0.0"}'
    })
    service.scan_profile("StatusTest")
    summaries = service.list_profiles()
    assert summaries[0].status == ProfileStatus.OK

    # 3. warnings: unresolved version placeholder produces Severity.WARNING
    create_jar(mods_dir / "warn_mod.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "warn_mod", "version": "${version}"}'
    })
    service.scan_profile("StatusTest")
    summaries = service.list_profiles()
    assert summaries[0].status == ProfileStatus.WARNINGS

    # 4. errors: corrupt jar file produces Severity.ERROR
    (mods_dir / "corrupt.jar").write_bytes(b"corrupt non-zip content")
    service.scan_profile("StatusTest")
    summaries = service.list_profiles()
    assert summaries[0].status == ProfileStatus.ERRORS

    # 5. path_missing after deleting directory
    import shutil
    shutil.rmtree(pack_dir)
    summaries = service.list_profiles()
    assert summaries[0].status == ProfileStatus.PATH_MISSING


def test_service_diff_and_pinning(tmp_path: Path):
    """Diff integration and pinning through ProfileService."""
    repo = ProfileRepository(tmp_path / "app.db")
    service = ProfileService(repo)

    pack_dir = tmp_path / "diff_pack"
    pack_dir.mkdir()
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir()

    create_jar(mods_dir / "mod1.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod1", "version": "1.0.0"}'
    })
    service.add_profile("DiffPack", pack_dir)

    # Diff with fewer than 2 scans -> ValueError
    with pytest.raises(ValueError) as exc_info:
        service.diff_scans("DiffPack")
    assert "fewer than two scans" in str(exc_info.value)

    s1 = service.scan_profile("DiffPack")

    # Add second mod and scan again
    create_jar(mods_dir / "mod2.jar", {
        "fabric.mod.json": '{"schemaVersion": 1, "id": "mod2", "version": "2.0.0"}'
    })
    s2 = service.scan_profile("DiffPack")

    # Default diff (previous s1 vs latest s2)
    diff_res = service.diff_scans("DiffPack")
    assert diff_res.has_changes
    assert len(diff_res.top_level_mods) == 1
    assert diff_res.top_level_mods[0].id == "mod2"
    assert diff_res.top_level_mods[0].change_type == "added"

    # Explicit scan IDs
    diff_explicit = service.diff_scans("DiffPack", s1.summary.id, s2.summary.id)
    assert diff_explicit.to_dict() == diff_res.to_dict()

    # Pinning
    service.set_scan_pinned("DiffPack", s1.summary.id, True)
    assert service.get_history("DiffPack")[1].pinned is True
    service.set_scan_pinned("DiffPack", s1.summary.id, False)
    assert service.get_history("DiffPack")[1].pinned is False


def test_concurrency_smoke(tmp_path: Path):
    """A9: Concurrency smoke - multiple threads scanning and listing profiles concurrently."""
    repo = ProfileRepository(tmp_path / "concurrent.db")
    service = ProfileService(repo)

    # Create 3 profiles
    pack_dirs: list[Path] = []
    for i in range(3):
        pdir = tmp_path / f"pack_{i}"
        pdir.mkdir()
        mods = pdir / "mods"
        create_jar(mods / f"mod_{i}.jar", {
            "fabric.mod.json": f'{{"schemaVersion": 1, "id": "mod_{i}", "version": "1.0.0"}}'
        })
        service.add_profile(f"Profile_{i}", pdir)
        pack_dirs.append(pdir)

    errors: list[Exception] = []

    def worker_scan(prof_name: str):
        try:
            for _ in range(4):
                service.scan_profile(prof_name)
        except Exception as exc:
            errors.append(exc)

    def worker_list():
        try:
            for _ in range(10):
                service.list_profiles()
        except Exception as exc:
            errors.append(exc)

    threads = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        for i in range(3):
            threads.append(executor.submit(worker_scan, f"Profile_{i}"))
        for _ in range(3):
            threads.append(executor.submit(worker_list))

    for t in threads:
        t.result()

    assert not errors, f"Concurrent operations encountered errors: {errors}"

    # Verify all scans persisted (4 scans * 3 profiles = 12 scans)
    total_scans = sum(len(service.get_history(f"Profile_{i}")) for i in range(3))
    assert total_scans == 12
