"""Parser for fabric.mod.json metadata files.

Pure functions: content in -> (mods, findings, nested_jar_paths) out.
"""

from __future__ import annotations

import json
from typing import Any

from mmis.domain.findings import (
    INVALID_METADATA_JSON,
    INVALID_VERSION_RANGE,
    LENIENT_JSON_REPAIRED,
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


def sanitize_lenient_json(raw: str) -> tuple[str, bool]:
    """Sanitize raw newlines, carriage returns, and tabs inside JSON string literals."""
    out: list[str] = []
    in_string = False
    escaped = False
    repaired = False

    for ch in raw:
        if in_string:
            if escaped:
                escaped = False
                out.append(ch)
            elif ch == "\\":
                escaped = True
                out.append(ch)
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ch == "\n":
                out.append("\\n")
                repaired = True
            elif ch == "\r":
                out.append("\\r")
                repaired = True
            elif ch == "\t":
                out.append("\\t")
                repaired = True
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)

    return "".join(out), repaired


def parse_fabric_mod_json(
    content: str | bytes,
    file_path: str = "fabric.mod.json",
    jar_path: str = "",
    parent_jar: str | None = None,
    is_nested: bool = False,
) -> tuple[list[ModInfo], list[Finding], list[str]]:
    """Parse fabric.mod.json content and return (mods, findings, nested_jar_paths)."""
    findings: list[Finding] = []
    mods: list[ModInfo] = []
    nested_jar_paths: list[str] = []

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            try:
                text = content.decode("latin-1")
            except Exception:
                findings.append(
                    Finding(
                        code=INVALID_METADATA_JSON,
                        severity=Severity.ERROR,
                        certainty=Certainty.CERTAIN,
                        message=f"Failed to decode {file_path}: {exc}",
                        evidence=[
                            Evidence(
                                file=jar_path or file_path,
                                field=file_path,
                                message=str(exc),
                            )
                        ],
                    )
                )
                return mods, findings, nested_jar_paths
    else:
        text = content

    repaired = False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        sanitized_text, repaired = sanitize_lenient_json(text)
        try:
            data = json.loads(sanitized_text)
        except json.JSONDecodeError as exc:
            findings.append(
                Finding(
                    code=INVALID_METADATA_JSON,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Malformed JSON in {file_path}: {exc}",
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
            return mods, findings, nested_jar_paths

    if repaired:
        findings.append(
            Finding(
                code=LENIENT_JSON_REPAIRED,
                severity=Severity.INFO,
                certainty=Certainty.CERTAIN,
                message=f"Repaired raw newlines or tabs in string literals in {file_path}",
                evidence=[
                    Evidence(
                        file=jar_path or file_path,
                        field=file_path,
                        message="Lenient JSON control characters repaired",
                    )
                ],
            )
        )

    if not isinstance(data, dict):
        findings.append(
            Finding(
                code=INVALID_METADATA_JSON,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Root of {file_path} must be a JSON object",
                evidence=[
                    Evidence(
                        file=jar_path or file_path,
                        field=file_path,
                        message="Root is not a JSON object",
                    )
                ],
            )
        )
        return mods, findings, nested_jar_paths

    mod_id = data.get("id")
    if not mod_id or not isinstance(mod_id, str):
        findings.append(
            Finding(
                code=INVALID_METADATA_JSON,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Missing or invalid 'id' field in {file_path}",
                evidence=[
                    Evidence(
                        file=jar_path or file_path,
                        field="id",
                        raw_value=str(mod_id) if mod_id is not None else None,
                        message="Mod id must be a non-empty string",
                    )
                ],
            )
        )
        return mods, findings, nested_jar_paths

    name = data.get("name")
    if name is not None and not isinstance(name, str):
        name = str(name)

    raw_version = data.get("version")
    version: str | None = None
    if raw_version is not None:
        raw_version_str = str(raw_version)
        if raw_version_str.startswith("${") and raw_version_str.endswith("}"):
            findings.append(
                Finding(
                    code=UNRESOLVED_PLACEHOLDER,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"Unresolved version placeholder in {file_path}: {raw_version_str}",
                    evidence=[
                        Evidence(
                            file=jar_path or file_path,
                            field="version",
                            raw_value=raw_version_str,
                            message="Placeholder could not be resolved",
                        )
                    ],
                    related_mod_ids=[mod_id],
                )
            )
            version = None
        else:
            version = raw_version_str

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)

    # Authors parsing (list of strings or list of objects {"name": "..."} or single string)
    raw_authors = data.get("authors")
    authors: list[str] = []
    if isinstance(raw_authors, list):
        for item in raw_authors:
            if isinstance(item, str):
                authors.append(item)
            elif isinstance(item, dict) and "name" in item:
                authors.append(str(item["name"]))
    elif isinstance(raw_authors, str):
        authors.append(raw_authors)

    # License
    raw_license = data.get("license")
    license_str: str | None = None
    if isinstance(raw_license, list):
        license_str = ", ".join(str(l) for l in raw_license)
    elif isinstance(raw_license, str):
        license_str = raw_license

    # Environment
    environment = data.get("environment")
    if environment is not None and not isinstance(environment, str):
        environment = str(environment)

    # Provides
    provides: list[str] = []
    raw_provides = data.get("provides")
    if isinstance(raw_provides, list):
        for p in raw_provides:
            if isinstance(p, str):
                provides.append(p)
            elif isinstance(p, dict) and "id" in p:
                provides.append(str(p["id"]))
    elif isinstance(raw_provides, dict):
        provides.extend(str(k) for k in raw_provides.keys())

    # Dependencies
    dependencies: list[Dependency] = []
    dep_categories: list[tuple[str, DependencyKind]] = [
        ("depends", DependencyKind.REQUIRED),
        ("recommends", DependencyKind.OPTIONAL),
        ("suggests", DependencyKind.OPTIONAL),
        ("conflicts", DependencyKind.CONFLICTS),
        ("breaks", DependencyKind.BREAKS),
    ]

    for cat_name, kind in dep_categories:
        deps_dict = data.get(cat_name)
        if isinstance(deps_dict, dict):
            for dep_id, raw_range in deps_dict.items():
                vr: VersionRange | None = None
                if raw_range is not None:
                    if isinstance(raw_range, list):
                        raw_range_str = json.dumps(raw_range)
                    else:
                        raw_range_str = str(raw_range)

                    if raw_range_str.strip():
                        vr = VersionRange.parse(raw_range_str, syntax="fabric")
                        if not vr.is_valid:
                            findings.append(
                                Finding(
                                    code=INVALID_VERSION_RANGE,
                                    severity=Severity.WARNING,
                                    certainty=Certainty.CERTAIN,
                                    message=f"Invalid version range '{raw_range_str}' for dependency '{dep_id}' in {file_path}",
                                    evidence=[
                                        Evidence(
                                            file=jar_path or file_path,
                                            field=f"{cat_name}.{dep_id}",
                                            raw_value=raw_range_str,
                                            message="Could not parse Fabric version predicate",
                                        )
                                    ],
                                    related_mod_ids=[mod_id, dep_id],
                                )
                            )

                dependencies.append(
                    Dependency(
                        mod_id=dep_id,
                        version_range=vr,
                        kind=kind,
                        declared_as=cat_name,
                    )
                )

    # Nested jars discovery
    raw_jars = data.get("jars")
    if isinstance(raw_jars, list):
        for j_entry in raw_jars:
            if isinstance(j_entry, dict) and "file" in j_entry:
                nested_jar_paths.append(str(j_entry["file"]))
            elif isinstance(j_entry, str):
                nested_jar_paths.append(j_entry)

    mod_info = ModInfo(
        id=mod_id,
        name=name,
        version=version,
        loaders=[Loader.FABRIC],
        provides=provides,
        dependencies=dependencies,
        jar_path=jar_path,
        metadata_file=file_path,
        description=description,
        authors=authors,
        license=license_str,
        environment=environment,
        parent_jar=parent_jar,
        related_files=[],
        is_nested=is_nested,
    )
    mods.append(mod_info)

    return mods, findings, nested_jar_paths
