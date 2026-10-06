"""Parser for Forge (mods.toml) and NeoForge (neoforge.mods.toml) metadata files.

Pure functions: content in -> (mods, findings) out.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from mmis.domain.findings import (
    INVALID_METADATA_TOML,
    INVALID_VERSION_RANGE,
    UNRESOLVED_PLACEHOLDER,
)
from mmis.domain.models import (
    Certainty,
    Dependency,
    DependencyKind,
    Evidence,
    Finding,
    Loader,
    ModInfo,
    Severity,
)
from mmis.domain.versions import VersionRange

_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}")


def parse_forge_toml(
    content: str | bytes,
    file_path: str,
    jar_path: str = "",
    manifest_version: str | None = None,
    parent_jar: str | None = None,
    is_nested: bool = False,
) -> tuple[list[ModInfo], list[Finding]]:
    """Parse mods.toml or neoforge.mods.toml and return (mods, findings)."""
    findings: list[Finding] = []
    mods: list[ModInfo] = []

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except Exception as exc:
                findings.append(
                    Finding(
                        code=INVALID_METADATA_TOML,
                        severity=Severity.ERROR,
                        certainty=Certainty.CERTAIN,
                        message=f"Failed to decode {file_path}: {exc}",
                        evidence=[Evidence(file=jar_path or file_path, field=file_path, message=str(exc))],
                    )
                )
                return mods, findings
    else:
        text = content

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        findings.append(
            Finding(
                code=INVALID_METADATA_TOML,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Malformed TOML in {file_path}: {exc}",
                evidence=[
                    Evidence(
                        file=jar_path or file_path,
                        field=file_path,
                        raw_value=text[:200],
                        message=str(exc),
                    )
                ],
            )
        )
        return mods, findings

    is_neoforge = "neoforge" in file_path.lower()
    loader_family = Loader.NEOFORGE if is_neoforge else Loader.FORGE

    top_license = data.get("license")
    if top_license is not None and not isinstance(top_license, str):
        top_license = str(top_license)

    raw_mods = data.get("mods")
    if not isinstance(raw_mods, list) or not raw_mods:
        findings.append(
            Finding(
                code=INVALID_METADATA_TOML,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"No [[mods]] entries found in {file_path}",
                evidence=[Evidence(file=jar_path or file_path, field="mods", message="Missing or empty [[mods]] list")],
            )
        )
        return mods, findings

    dependencies_table = data.get("dependencies", {})
    if not isinstance(dependencies_table, dict):
        dependencies_table = {}

    for mod_table in raw_mods:
        if not isinstance(mod_table, dict):
            continue

        mod_id = mod_table.get("modId")
        if not mod_id or not isinstance(mod_id, str):
            findings.append(
                Finding(
                    code=INVALID_METADATA_TOML,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Missing or invalid 'modId' in {file_path}",
                    evidence=[
                        Evidence(
                            file=jar_path or file_path,
                            field="modId",
                            raw_value=str(mod_id) if mod_id is not None else None,
                            message="ModId must be a non-empty string",
                        )
                    ],
                )
            )
            continue

        name = mod_table.get("displayName")
        if name is not None and not isinstance(name, str):
            name = str(name)

        raw_version = mod_table.get("version")
        version: str | None = None
        if raw_version is not None:
            raw_v_str = str(raw_version).strip()
            # Try to resolve ${file.jarVersion}
            if "${file.jarVersion}" in raw_v_str:
                if manifest_version:
                    raw_v_str = raw_v_str.replace("${file.jarVersion}", manifest_version)

            # Check if any unresolved placeholder remains
            if _PLACEHOLDER_RE.search(raw_v_str):
                findings.append(
                    Finding(
                        code=UNRESOLVED_PLACEHOLDER,
                        severity=Severity.WARNING,
                        certainty=Certainty.CERTAIN,
                        message=f"Unresolved version placeholder in {file_path}: {raw_version}",
                        evidence=[
                            Evidence(
                                file=jar_path or file_path,
                                field=f"mods.{mod_id}.version",
                                raw_value=str(raw_version),
                                message="Placeholder could not be resolved from manifest",
                            )
                        ],
                        related_mod_ids=[mod_id],
                    )
                )
                version = None
            else:
                version = raw_v_str

        description = mod_table.get("description")
        if description is not None and not isinstance(description, str):
            description = str(description)

        raw_authors = mod_table.get("authors")
        authors: list[str] = []
        if isinstance(raw_authors, str):
            authors = [a.strip() for a in raw_authors.split(",") if a.strip()]
        elif isinstance(raw_authors, list):
            authors = [str(a).strip() for a in raw_authors if str(a).strip()]

        mod_license = mod_table.get("license") or top_license
        if mod_license is not None and not isinstance(mod_license, str):
            mod_license = str(mod_license)

        side = mod_table.get("side")
        environment = str(side) if side is not None else None

        # Process dependencies
        mod_deps: list[Dependency] = []
        dep_list = dependencies_table.get(mod_id, [])
        if isinstance(dep_list, list):
            for dep_spec in dep_list:
                if not isinstance(dep_spec, dict):
                    continue
                dep_mod_id = dep_spec.get("modId")
                if not dep_mod_id or not isinstance(dep_mod_id, str):
                    continue

                raw_vr = dep_spec.get("versionRange")
                raw_vr_str = str(raw_vr).strip() if raw_vr is not None else ""

                vr: VersionRange | None = None
                if raw_vr_str:
                    vr = VersionRange.parse(raw_vr_str, syntax="maven")
                    if not vr.is_valid:
                        findings.append(
                            Finding(
                                code=INVALID_VERSION_RANGE,
                                severity=Severity.WARNING,
                                certainty=Certainty.CERTAIN,
                                message=f"Invalid Maven version range '{raw_vr_str}' for dependency '{dep_mod_id}' in {file_path}",
                                evidence=[
                                    Evidence(
                                        file=jar_path or file_path,
                                        field=f"dependencies.{mod_id}.{dep_mod_id}.versionRange",
                                        raw_value=raw_vr_str,
                                        message="Could not parse Maven version range",
                                    )
                                ],
                                related_mod_ids=[mod_id, dep_mod_id],
                            )
                        )

                # Determine dependency kind
                if is_neoforge:
                    dep_type = str(dep_spec.get("type", "required")).lower()
                    if dep_type == "optional":
                        kind = DependencyKind.OPTIONAL
                    elif dep_type in ("incompatible", "breaks"):
                        kind = DependencyKind.BREAKS
                    elif dep_type in ("discouraged", "conflicts"):
                        kind = DependencyKind.CONFLICTS
                    else:
                        kind = DependencyKind.REQUIRED
                    declared_as = dep_type
                else:
                    mandatory = dep_spec.get("mandatory", True)
                    if not mandatory:
                        kind = DependencyKind.OPTIONAL
                        declared_as = "mandatory=false"
                    else:
                        kind = DependencyKind.REQUIRED
                        declared_as = "mandatory=true"

                mod_deps.append(
                    Dependency(
                        mod_id=dep_mod_id,
                        version_range=vr,
                        kind=kind,
                        declared_as=declared_as,
                    )
                )

        mods.append(
            ModInfo(
                id=mod_id,
                name=name,
                version=version,
                loaders=[loader_family],
                provides=[],
                dependencies=mod_deps,
                jar_path=jar_path,
                metadata_file=file_path,
                description=description,
                authors=authors,
                license=mod_license,
                environment=environment,
                parent_jar=parent_jar,
                related_files=[],
                is_nested=is_nested,
            )
        )

    return mods, findings
