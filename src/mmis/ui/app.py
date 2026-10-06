"""Application startup, QApplication initialization, and stylesheet bootstrap."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mmis.services.paths import resolve_data_dir
from mmis.services.profiles import ProfileService, create_profile_service
from mmis.ui import strings
from mmis.ui.shell import MainWindow
from mmis.ui.theme.qss import build_stylesheet
from mmis.ui.workers import setup_ui_logging


def create_app(args: list[str] | None = None) -> QApplication:
    """Create or retrieve existing QApplication instance with global styling."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(args or sys.argv[:1])

    app.setApplicationName(strings.APP_TITLE)
    app.setStyleSheet(build_stylesheet())
    return app


def launch_gui(data_dir: str | Path | None = None, auto_close_ms: int | None = None) -> int:
    """Initialize services, launch the main window, and execute the Qt event loop."""
    # Ensure Windows fonts in offscreen mode
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.name == "nt":
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    resolved_dir = resolve_data_dir(data_dir)

    setup_ui_logging(resolved_dir)

    app = create_app()

    service = create_profile_service(resolved_dir)

    window = MainWindow(service=service, data_dir=resolved_dir)
    window.show()

    # Optional auto-close timer for automated health checks
    env_close = os.environ.get("MMIS_AUTO_CLOSE_MS")
    close_delay = auto_close_ms or (int(env_close) if env_close and env_close.isdigit() else None)
    if close_delay is not None and close_delay > 0:
        QTimer.singleShot(close_delay, window.close)

    return app.exec()
