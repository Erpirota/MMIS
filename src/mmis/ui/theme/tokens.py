"""Design system tokens for MMIS: colors, spacing, typography, radii, and motion.

This file is the single source of truth for all visual parameters in the application.
"""

from __future__ import annotations

import os

# --- Colour Roles (Graphite Dark Theme + Muted Green Accent) ---
BG_WINDOW = "#0F1115"
SURFACE_1 = "#161A20"       # Sidebars, cards, panels
SURFACE_2 = "#1E232B"       # Elevated controls, table headers, hover backgrounds
SURFACE_3 = "#262C36"       # Modals, floating toast overlays
BORDER_HAIRLINE = "#2D333B" # Subtle structural dividing lines
BORDER_FOCUS = "#3FB950"    # Keyboard focus ring

# Typography colours
TEXT_PRIMARY = "#E6EDF3"    # Body text, titles, prominent data
TEXT_SECONDARY = "#9DA7B3"  # Subtitles, section headers, metadata
TEXT_MUTED = "#8B949E"      # Captions, placeholders, disabled text
TEXT_ON_ACCENT = "#0F1115"  # High-contrast text on primary buttons

# Accent (Muted Green)
ACCENT = "#3FB950"
ACCENT_HOVER = "#46C65A"
ACCENT_PRESSED = "#339B42"
ACCENT_BG = "#132D1B"       # Subtle badge or selection background

# Semantic status colours
COLOR_ERROR = "#F85149"
COLOR_ERROR_BG = "#381B1B"
COLOR_WARNING = "#D29922"
COLOR_WARNING_BG = "#332615"
COLOR_INFO = "#58A6FF"
COLOR_INFO_BG = "#162842"
COLOR_SUCCESS = "#3FB950"
COLOR_SUCCESS_BG = "#132D1B"
COLOR_DISABLED = "#6E7681"
COLOR_DISABLED_BG = "#1C2128"

# --- Spacing Scale (4, 8, 12, 16, 24, 32) ---
SPACE_XXS = 4
SPACE_XS = 8
SPACE_SM = 12
SPACE_MD = 16
SPACE_LG = 24
SPACE_XL = 32

# --- Radii ---
RADIUS_SM = 4
RADIUS_MD = 8
RADIUS_LG = 12
RADIUS_PILL = 999

# --- Typography Scale (12, 13, 14, 16, 20, 28) ---
FONT_FAMILY = "Segoe UI Variable, Segoe UI, -apple-system, BlinkMacSystemFont, Roboto, sans-serif"
FONT_SIZE_XS = 11
FONT_SIZE_SM = 12
FONT_SIZE_BASE = 13
FONT_SIZE_MD = 14
FONT_SIZE_LG = 16
FONT_SIZE_XL = 20
FONT_SIZE_XXL = 28

# Font weights
FONT_WEIGHT_NORMAL = 400
FONT_WEIGHT_MEDIUM = 500
FONT_WEIGHT_SEMIBOLD = 600
FONT_WEIGHT_BOLD = 700

# --- Motion Scale (120 - 220 ms) ---
DURATION_SHORT_MS = 120
DURATION_NORMAL_MS = 180
DURATION_LONG_MS = 220


def get_animation_duration(base_duration_ms: int) -> int:
    """Return animation duration in ms, respecting MMIS_NO_ANIM environment variable."""
    if os.environ.get("MMIS_NO_ANIM") == "1":
        return 0
    return base_duration_ms


# --- Accessibility & Contrast Utilities ---
def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    clean = hex_code.lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = [c / 255.0 for c in rgb]

    def adjust(channel: float) -> float:
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def calculate_contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG 2.1 relative contrast ratio between two hex colours."""
    l1 = _relative_luminance(_hex_to_rgb(hex1))
    l2 = _relative_luminance(_hex_to_rgb(hex2))
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
