"""Jar archive reader and nested jar inspector.

Handles zip validation, manifest extraction, nested jars, and size/depth limits.
Pure stdlib, resource safe.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from typing import Any

from mmis.domain.findings import (
    CORRUPT_JAR,
    LEGACY_MOD_INFO,
    METADATA_TOO_LARGE,
    NESTED_JAR_DEPTH_EXCEEDED,
    NESTED_JAR_SIZE_EXCEEDED,
    NO_METADATA,
    NOT_A_ZIP,
)
from mmis.domain.models import (
    Certainty,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    Severity,
)
from mmis.minecraft.fabric import parse_fabric_mod_json
from mmis.minecraft.forge import parse_forge_toml
from mmis.minecraft.quilt import parse_quilt_mod_json

MAX_METADATA_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB
MAX_NESTED_DEPTH = 3
MAX_NESTED_SIZE_BYTES = 64 * 1024 * 1024  # 64 MB

_MANIFEST_IMPL_VER_RE = re.compile(r"^Implementation-Version:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_MANIFEST_SPEC_VER_RE = re.compile(r"^Specification-Version:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def extract_manifest_version(manifest_bytes: bytes) -> str | None:
    """Extract Implementation-Version or Specification-Version from MANIFEST.MF bytes."""
    try:
        text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = manifest_bytes.decode("latin-1")
        except Exception:
            return None

    # Handle line wrapping in MANIFEST.MF (lines beginning with space are continuations)
    unwrapped_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(" ") and unwrapped_lines:
            unwrapped_lines[-1] += line[1:]
        else:
            unwrapped_lines.append(line)
    full_manifest = "\n".join(unwrapped_lines)

    m_impl = _MANIFEST_IMPL_VER_RE.search(full_manifest)
    if m_impl:
        val = m_impl.group(1).strip()
        if val:
            return val

    m_spec = _MANIFEST_SPEC_VER_RE.search(full_manifest)
    if m_spec:
        val = m_spec.group(1).strip()
        if val:
            return val

    return None


def _read_metadata_entry(
    zf: zipfile.ZipFile,
    entry_name: str,
    display_path: str,
    severity: Severity = Severity.ERROR,
) -> tuple[bytes | None, Finding | None]:
    """Read a metadata entry up to MAX_METADATA_SIZE_BYTES decompressed bytes.

    Returns (content_bytes, None) if within limit, or (None, finding) if refused.
    """
    info = zf.getinfo(entry_name)
    if info.file_size > MAX_METADATA_SIZE_BYTES:
        finding = Finding(
            code=METADATA_TOO_LARGE,
            severity=severity,
            certainty=Certainty.CERTAIN,
            message=f"Metadata entry '{entry_name}' exceeds size limit of {MAX_METADATA_SIZE_BYTES} bytes in {display_path} ({info.file_size} bytes)",
            evidence=[
                Evidence(
                    file=display_path,
                    field=entry_name,
                    raw_value=str(info.file_size),
                    message=f"Metadata entry '{entry_name}' size {info.file_size} exceeds limit {MAX_METADATA_SIZE_BYTES} bytes",
                )
            ],
        )
        return None, finding

    with zf.open(info) as f:
        data = f.read(MAX_METADATA_SIZE_BYTES + 1)

    if len(data) > MAX_METADATA_SIZE_BYTES:
        finding = Finding(
            code=METADATA_TOO_LARGE,
            severity=severity,
            certainty=Certainty.CERTAIN,
            message=f"Metadata entry '{entry_name}' exceeds size limit of {MAX_METADATA_SIZE_BYTES} bytes in {display_path}",
            evidence=[
                Evidence(
                    file=display_path,
                    field=entry_name,
                    raw_value=str(info.file_size),
                    message=f"Metadata entry '{entry_name}' size exceeds limit {MAX_METADATA_SIZE_BYTES} bytes",
                )
            ],
        )
        return None, finding

    return data, None


def _process_zip_contents(
    zf: zipfile.ZipFile,
    display_path: str,
    size_bytes: int,
    sha256: str,
    is_disabled: bool,
    parent_path: str | None,
    depth: int,
) -> tuple[JarFile, list[ModInfo], list[Finding], list[tuple[str, bytes]]]:
    """Process an opened zip file's contents, extract metadata and nested jars."""
    all_names = zf.namelist()
    namelist_set = set(all_names)

    # Check for corrupt archive entries
    bad_file = zf.testzip()
    if bad_file:
        finding = Finding(
            code=CORRUPT_JAR,
            severity=Severity.ERROR,
            certainty=Certainty.CERTAIN,
            message=f"Corrupt entry '{bad_file}' in archive {display_path}",
            evidence=[Evidence(file=display_path, field=bad_file, message="CRC check failed")],
        )
        jar_file = JarFile(
            path=display_path,
            size_bytes=size_bytes,
            sha256=sha256,
            is_disabled=is_disabled,
            mods=[],
            is_nested=parent_path is not None,
            parent_path=parent_path,
        )
        return jar_file, [], [finding], []

    mods: list[ModInfo] = []
    findings: list[Finding] = []
    nested_jar_names: set[str] = set()
    found_metadata = False

    # Read manifest if present
    manifest_version: str | None = None
    manifest_path = "META-INF/MANIFEST.MF"
    for name in namelist_set:
        if name.upper() == manifest_path:
            try:
                m_data, m_finding = _read_metadata_entry(zf, name, display_path, severity=Severity.WARNING)
                if m_finding is not None:
                    findings.append(m_finding)
                elif m_data is not None:
                    manifest_version = extract_manifest_version(m_data)
            except (KeyError, zipfile.BadZipFile, OSError):
                pass
            break

    # 1. Fabric: fabric.mod.json
    if "fabric.mod.json" in namelist_set:
        found_metadata = True
        try:
            content, f_finding = _read_metadata_entry(zf, "fabric.mod.json", display_path)
            if f_finding is not None:
                findings.append(f_finding)
            elif content is not None:
                f_mods, f_findings, f_nested = parse_fabric_mod_json(
                    content,
                    file_path="fabric.mod.json",
                    jar_path=display_path,
                    parent_jar=parent_path,
                    is_nested=parent_path is not None,
                )
                mods.extend(f_mods)
                findings.extend(f_findings)
                nested_jar_names.update(f_nested)
        except Exception as exc:
            findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Error reading fabric.mod.json in {display_path}: {exc}",
                    evidence=[Evidence(file=display_path, field="fabric.mod.json", message=str(exc))],
                )
            )

    # 2. Quilt: quilt.mod.json
    if "quilt.mod.json" in namelist_set:
        found_metadata = True
        try:
            content, q_finding = _read_metadata_entry(zf, "quilt.mod.json", display_path)
            if q_finding is not None:
                findings.append(q_finding)
            elif content is not None:
                q_mods, q_findings, q_nested = parse_quilt_mod_json(
                    content,
                    file_path="quilt.mod.json",
                    jar_path=display_path,
                    parent_jar=parent_path,
                    is_nested=parent_path is not None,
                )
                mods.extend(q_mods)
                findings.extend(q_findings)
                nested_jar_names.update(q_nested)
        except Exception as exc:
            findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Error reading quilt.mod.json in {display_path}: {exc}",
                    evidence=[Evidence(file=display_path, field="quilt.mod.json", message=str(exc))],
                )
            )

    # 3. NeoForge: META-INF/neoforge.mods.toml
    neoforge_path = None
    for name in namelist_set:
        if name.lower() == "meta-inf/neoforge.mods.toml":
            neoforge_path = name
            break
    if neoforge_path:
        found_metadata = True
        try:
            content, nf_finding = _read_metadata_entry(zf, neoforge_path, display_path)
            if nf_finding is not None:
                findings.append(nf_finding)
            elif content is not None:
                nf_mods, nf_findings = parse_forge_toml(
                    content,
                    file_path=neoforge_path,
                    jar_path=display_path,
                    manifest_version=manifest_version,
                    parent_jar=parent_path,
                    is_nested=parent_path is not None,
                )
                mods.extend(nf_mods)
                findings.extend(nf_findings)
        except Exception as exc:
            findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Error reading {neoforge_path} in {display_path}: {exc}",
                    evidence=[Evidence(file=display_path, field=neoforge_path, message=str(exc))],
                )
            )

    # 4. Forge: META-INF/mods.toml
    forge_path = None
    for name in namelist_set:
        if name.lower() == "meta-inf/mods.toml":
            forge_path = name
            break
    if forge_path:
        found_metadata = True
        try:
            content, fg_finding = _read_metadata_entry(zf, forge_path, display_path)
            if fg_finding is not None:
                findings.append(fg_finding)
            elif content is not None:
                fg_mods, fg_findings = parse_forge_toml(
                    content,
                    file_path=forge_path,
                    jar_path=display_path,
                    manifest_version=manifest_version,
                    parent_jar=parent_path,
                    is_nested=parent_path is not None,
                )
                mods.extend(fg_mods)
                findings.extend(fg_findings)
        except Exception as exc:
            findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Error reading {forge_path} in {display_path}: {exc}",
                    evidence=[Evidence(file=display_path, field=forge_path, message=str(exc))],
                )
            )

    # Check for legacy mcmod.info or complete absence of metadata
    if not found_metadata:
        has_mcmod_info = any(name.lower() in ("mcmod.info", "meta-inf/mcmod.info") for name in namelist_set)
        if has_mcmod_info:
            findings.append(
                Finding(
                    code=LEGACY_MOD_INFO,
                    severity=Severity.INFO,
                    certainty=Certainty.CERTAIN,
                    message=f"Legacy mcmod.info metadata is out of scope and not parsed: {display_path}",
                    evidence=[Evidence(file=display_path, message="Found legacy mcmod.info format")],
                )
            )
        else:
            findings.append(
                Finding(
                    code=NO_METADATA,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"No supported mod metadata found in {display_path}",
                    evidence=[Evidence(file=display_path, message="No fabric, quilt, or forge metadata found")],
                )
            )

    # Discover additional nested jars from META-INF/jars/ and META-INF/jarjar/
    for name in all_names:
        lower_name = name.lower()
        if (
            lower_name.startswith("meta-inf/jars/")
            or lower_name.startswith("meta-inf/jarjar/")
        ) and lower_name.endswith(".jar"):
            nested_jar_names.add(name)

    # Process nested jars
    nested_to_process: list[tuple[str, bytes]] = []
    for n_name in sorted(nested_jar_names):
        clean_name = n_name.lstrip("/")
        if clean_name not in namelist_set:
            continue

        nested_display_path = f"{display_path}!/{clean_name}"

        # Check depth limit
        if depth + 1 > MAX_NESTED_DEPTH:
            findings.append(
                Finding(
                    code=NESTED_JAR_DEPTH_EXCEEDED,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"Nested jar exceeds maximum depth {MAX_NESTED_DEPTH}: {nested_display_path}",
                    evidence=[
                        Evidence(
                            file=nested_display_path,
                            raw_value=str(depth + 1),
                            message=f"Depth {depth + 1} exceeds limit {MAX_NESTED_DEPTH}",
                        )
                    ],
                )
            )
            continue

        try:
            info = zf.getinfo(clean_name)
            if info.file_size > MAX_NESTED_SIZE_BYTES:
                findings.append(
                    Finding(
                        code=NESTED_JAR_SIZE_EXCEEDED,
                        severity=Severity.WARNING,
                        certainty=Certainty.CERTAIN,
                        message=f"Nested jar exceeds size limit of 64MB: {nested_display_path} ({info.file_size} bytes)",
                        evidence=[
                            Evidence(
                                file=nested_display_path,
                                raw_value=str(info.file_size),
                                message=f"Size {info.file_size} exceeds {MAX_NESTED_SIZE_BYTES} bytes",
                            )
                        ],
                    )
                )
                continue

            n_bytes = zf.read(clean_name)
            nested_to_process.append((nested_display_path, n_bytes))
        except Exception as exc:
            findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Error reading nested jar {clean_name} in {display_path}: {exc}",
                    evidence=[Evidence(file=display_path, field=clean_name, message=str(exc))],
                )
            )

    jar_file = JarFile(
        path=display_path,
        size_bytes=size_bytes,
        sha256=sha256,
        is_disabled=is_disabled,
        mods=[m.id for m in mods],
        is_nested=parent_path is not None,
        parent_path=parent_path,
    )

    return jar_file, mods, findings, nested_to_process


def read_jar(
    data_or_path: bytes | str | os.PathLike[str],
    display_path: str,
    is_disabled: bool = False,
    parent_path: str | None = None,
    depth: int = 0,
) -> tuple[JarFile, list[ModInfo], list[Finding], list[tuple[str, bytes]]]:
    """Read a jar file from bytes in memory or from a filesystem path.

    Returns:
        (JarFile, mods, findings, nested_jars_to_process)
        where nested_jars_to_process is a list of (nested_display_path, nested_bytes).
    """
    if isinstance(data_or_path, (str, os.PathLike)):
        file_path = os.fspath(data_or_path)
        if os.path.isdir(file_path):
            finding = Finding(
                code=CORRUPT_JAR,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Expected jar file but found directory: {display_path}",
                evidence=[Evidence(file=display_path, message="Path is a directory")],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=0,
                sha256="",
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

        try:
            size_bytes = os.path.getsize(file_path)
            h = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            sha256 = h.hexdigest()
        except Exception as exc:
            finding = Finding(
                code=CORRUPT_JAR,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Could not read jar file {display_path}: {exc}",
                evidence=[Evidence(file=display_path, message=str(exc))],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=0,
                sha256="",
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

        if not zipfile.is_zipfile(file_path):
            try:
                with open(file_path, "rb") as f:
                    starts_with_pk = f.read(2).startswith(b"PK")
            except Exception:
                starts_with_pk = False

            if starts_with_pk:
                code = CORRUPT_JAR
                msg = f"Corrupt or incomplete zip archive: {display_path}"
            else:
                code = NOT_A_ZIP
                msg = f"File is not a valid zip archive: {display_path}"

            finding = Finding(
                code=code,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=msg,
                evidence=[Evidence(file=display_path, message="Failed zip validation")],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

        try:
            zf = zipfile.ZipFile(file_path, "r")
        except Exception as exc:
            finding = Finding(
                code=CORRUPT_JAR,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to open zip archive {display_path}: {exc}",
                evidence=[Evidence(file=display_path, message=str(exc))],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

    else:
        jar_data = data_or_path
        size_bytes = len(jar_data)
        sha256 = hashlib.sha256(jar_data).hexdigest()

        # Verify if valid zip file
        if not zipfile.is_zipfile(io.BytesIO(jar_data)):
            if jar_data.startswith(b"PK"):
                code = CORRUPT_JAR
                msg = f"Corrupt or incomplete zip archive: {display_path}"
            else:
                code = NOT_A_ZIP
                msg = f"File is not a valid zip archive: {display_path}"

            finding = Finding(
                code=code,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=msg,
                evidence=[Evidence(file=display_path, message="Failed zip validation")],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

        try:
            zf = zipfile.ZipFile(io.BytesIO(jar_data), "r")
        except Exception as exc:
            finding = Finding(
                code=CORRUPT_JAR,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to open zip archive {display_path}: {exc}",
                evidence=[Evidence(file=display_path, message=str(exc))],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []

    try:
        try:
            return _process_zip_contents(
                zf=zf,
                display_path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                parent_path=parent_path,
                depth=depth,
            )
        except Exception as exc:
            finding = Finding(
                code=CORRUPT_JAR,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Error reading zip archive {display_path}: {exc}",
                evidence=[Evidence(file=display_path, message=str(exc))],
            )
            jar_file = JarFile(
                path=display_path,
                size_bytes=size_bytes,
                sha256=sha256,
                is_disabled=is_disabled,
                mods=[],
                is_nested=parent_path is not None,
                parent_path=parent_path,
            )
            return jar_file, [], [finding], []
    finally:
        zf.close()
