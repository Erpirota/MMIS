"""Session fixtures for offscreen Qt UI testing."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Ensure a singleton QApplication instance exists for offscreen UI tests."""
    from PySide6.QtWidgets import QApplication
    from mmis.ui.app import create_app

    app = QApplication.instance()
    if app is None:
        app = create_app()
    return app
