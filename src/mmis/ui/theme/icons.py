"""Icon loading and token tinting using hand-crafted SVG assets."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from mmis.ui.theme import tokens

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


@lru_cache(maxsize=128)
def get_svg_content(name: str) -> str:
    """Read SVG file content by name."""
    file_path = ASSETS_DIR / f"{name}.svg"
    if not file_path.is_file():
        # Fallback to an empty 24x24 SVG
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>'
    return file_path.read_text(encoding="utf-8")


def get_tinted_pixmap(
    name: str,
    color_hex: str = tokens.TEXT_PRIMARY,
    size: int = 18,
) -> QPixmap:
    """Render an SVG tinted with a specific token color to a QPixmap."""
    raw_svg = get_svg_content(name)
    tinted_svg = raw_svg.replace("currentColor", color_hex)
    byte_array = QByteArray(tinted_svg.encode("utf-8"))

    renderer = QSvgRenderer(byte_array)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()

    return pixmap


def get_tinted_icon(
    name: str,
    color_hex: str = tokens.TEXT_PRIMARY,
    size: int = 18,
) -> QIcon:
    """Return a QIcon tinted with the given color token."""
    pixmap = get_tinted_pixmap(name, color_hex=color_hex, size=size)
    return QIcon(pixmap)
