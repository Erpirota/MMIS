import os
from pathlib import Path
import pytest

# Tests never open real windows. The offscreen platform ships no fonts on Windows,
# so point it at the system font directory or text will not be rendered.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Safety net isolating test suite from real user profile and directories.

    Redirects APPDATA, USERPROFILE, HOME, MMIS_DATA_DIR, and Path.home()
    to a temporary path for each test execution.
    """
    fake_appdata = tmp_path / "_env_appdata"
    fake_appdata.mkdir(parents=True, exist_ok=True)
    fake_home = tmp_path / "_env_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_mmis_data = tmp_path / "_env_mmis_data"
    fake_mmis_data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("MMIS_DATA_DIR", str(fake_mmis_data))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
