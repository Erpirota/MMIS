"""Shared test helpers for synthetic jar creation in tmp_path."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def create_jar(
    jar_path: Path | str,
    files: dict[str, str | bytes],
) -> Path:
    """Create a synthetic zip/jar file in tmp_path."""
    p = Path(jar_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry_name, content in files.items():
            if isinstance(content, str):
                data = content.encode("utf-8")
            else:
                data = content
            zf.writestr(entry_name, data)
    return p


def create_nested_jar_bytes(files: dict[str, str | bytes]) -> bytes:
    """Create in-memory bytes of a nested jar file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry_name, content in files.items():
            if isinstance(content, str):
                data = content.encode("utf-8")
            else:
                data = content
            zf.writestr(entry_name, data)
    return buf.getvalue()
