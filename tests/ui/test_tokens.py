"""Design system token contrast and color literal boundary tests (A2)."""

from __future__ import annotations

from pathlib import Path
import re

from mmis.ui.theme import tokens


def test_theme_token_contrast_compliance():
    """Verify that body text and muted text satisfy WCAG contrast requirements."""
    surfaces = [tokens.BG_WINDOW, tokens.SURFACE_1, tokens.SURFACE_2, tokens.SURFACE_3]

    # Body text contrast against surfaces must be >= 4.5:1
    for surface in surfaces:
        ratio = tokens.calculate_contrast_ratio(tokens.TEXT_PRIMARY, surface)
        assert ratio >= 4.5, f"TEXT_PRIMARY on {surface} contrast ratio {ratio:.2f} is below 4.5:1"

    # Muted text contrast against primary surfaces must be >= 4.5:1
    for surface in [tokens.BG_WINDOW, tokens.SURFACE_1, tokens.SURFACE_2]:
        ratio = tokens.calculate_contrast_ratio(tokens.TEXT_MUTED, surface)
        assert ratio >= 4.5, f"TEXT_MUTED on {surface} contrast ratio {ratio:.2f} is below 4.5:1"

    # Large text title contrast against primary surface >= 3.0:1
    accent_ratio = tokens.calculate_contrast_ratio(tokens.ACCENT, tokens.SURFACE_1)
    assert accent_ratio >= 3.0, f"ACCENT on SURFACE_1 contrast ratio {accent_ratio:.2f} is below 3.0:1"

    # Text on primary accent button must have >= 4.5:1 contrast
    btn_text_ratio = tokens.calculate_contrast_ratio(tokens.TEXT_ON_ACCENT, tokens.ACCENT)
    assert btn_text_ratio >= 4.5, f"TEXT_ON_ACCENT on ACCENT contrast ratio {btn_text_ratio:.2f} is below 4.5:1"


def test_no_hardcoded_hex_colors_in_views_and_widgets():
    """Verify that pages, widgets, and components do not define hard-coded hex colors."""
    ui_dir = Path(__file__).resolve().parent.parent.parent / "src" / "mmis" / "ui"
    hex_pattern = re.compile(r'#[0-9a-fA-F]{3,8}\b')

    checked_dirs = [
        ui_dir / "pages",
        ui_dir / "widgets",
        ui_dir / "components",
    ]

    violations = []
    for d in checked_dirs:
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            matches = hex_pattern.findall(content)
            if matches:
                violations.append((py_file.name, matches))

    assert not violations, f"Hardcoded hex colors found outside theme tokens: {violations}"
