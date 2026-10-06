"""Parser for quilt.mod.json metadata files.

Pure functions: content in -> (mods, findings, nested_jar_paths) out.
Conforms to quilt.mod.json specification.
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
from mmis.minecraft.fabric import sanitize_lenient_json


def _parse_quilt_dep(
    dep_item: Any,
    default_kind: DependencyKind,
    declared_as: str,
    mod_id: str,
    file_path: str,
    jar_path: str,
) -> tuple[Dependency | None, list[Finding]]:
    findings: list[Finding] = []
    if isinstance(dep_item, str):
        dep = Dependency(
            mod_id=dep_item,
            version_range=None,
            kind=default_kind,
            declared_as=declared_as,
        )
        return dep, findings

    if not isinstance(dep_item, dict) or "id" not in dep_item:
        return None, findings

    target_id = str(dep_item["id"])
    v_spec = dep_item.get("versions")
    vr: VersionRange | None = None

    if v_spec is not None:
        if isinstance(v_spec, str):
            vr = VersionRange.parse(v_spec, syntax="fabric")
            if not vr.is_valid:
                findings.append(
                    Finding(
                        code=INVALID_VERSION_RANGE,
                        severity=Severity.WARNING,
                        certainty=Certainty.CERTAIN,
                        message=f"Invalid version range '{v_spec}' for dependency '{target_id}' in {file_path}",
                        evidence=[
                            Evidence(
                                file=jar_path or file_path,
                                field=f"quilt_loader.{declared_as}.{target_id}",
                                raw_value=v_spec,
                                message="Could not parse Fabric-compatible version predicate",
                            )
                        ],
                        related_mod_ids=[mod_id, target_id],
                    )
                )
        elif isinstance(v_spec, list):
            raw_json = json.dumps(v_spec)
            vr = VersionRange.parse(raw_json, syntax="fabric")
            if not vr.is_valid:
                findings.append(
                    Finding(
                        code=INVALID_VERSION_RANGE,
                        severity=Severity.WARNING,
                        certainty=Certainty.CERTAIN,
                        message=f"Invalid version list '{raw_json}' for dependency '{target_id}' in {file_path}",
                        evidence=[
                            Evidence(
                                file=jar_path or file_path,
                                field=f"quilt_loader.{declared_as}.{target_id}",
                                raw_value=raw_json,
                                message="Could not parse Fabric-compatible version list",
                            )
                        ],
                        related_mod_ids=[mod_id, target_id],
                    )
                )
        elif isinstance(v_spec, dict):
            # Complex versions object (e.g. {"any": [...]} / {"all": [...]})
            raw_json = json.dumps(v_spec)
            vr = VersionRange(raw=raw_json, syntax="fabric", parsed=None, is_valid=False)
            findings.append(
                Finding(
                    code=INVALID_VERSION_RANGE,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"Unsupported versions object structure '{raw_json}' for dependency '{target_id}' in {file_path}",
                    evidence=[
                        Evidence(
                            file=jar_path or file_path,
                            field=f"quilt_loader.{declared_as}.{target_id}",
                            raw_value=raw_json,
                            message="Complex versions object kept raw and flagged",
                        )
                    ],
                    related_mod_ids=[mod_id, target_id],
                )
            )
        else:
            raw_str = str(v_spec)
            vr = VersionRange(raw=raw_str, syntax="fabric", parsed=None, is_valid=False)
            findings.append(
                Finding(
                    code=INVALID_VERSION_RANGE,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"Invalid versions format '{raw_str}' for dependency '{target_id}' in {file_path}",
                    evidence=[
                        Evidence(
                            file=jar_path or file_path,
                            field=f"quilt_loader.{declared_as}.{target_id}",
                            raw_value=raw_str,
                            message="Invalid version format",
                        )
                    ],
                    related_mod_ids=[mod_id, target_id],
                )
            )

    is_opt = bool(dep_item.get("optional", False))
    is_break = bool(dep_item.get("breaks", False))
    is_conflict = bool(dep_item.get("conflicts", False))

    if declared_as == "breaks" or is_break:
        kind = DependencyKind.BREAKS
        decl_name = "breaks"
    elif is_conflict:
        kind = DependencyKind.CONFLICTS
        decl_name = "depends"
    elif is_opt:
        kind = DependencyKind.OPTIONAL
        decl_name = "depends"
    else:
        kind = default_kind
        decl_name = declared_as

    dep = Dependency(
        mod_id=target_id,
        version_range=vr,
        kind=kind,
        declared_as=decl_name,
    )
    return dep, findings


def parse_quilt_mod_json(
    content: str | bytes,
    file_path: str = "quilt.mod.json",
    jar_path: str = "",
    parent_jar: str | None = None,
    is_nested: bool = False,
) -> tuple[list[ModInfo], list[Finding], list[str]]:
    """Parse quilt.mod.json content and return (mods, findings, nested_jar_paths)."""
    findings: list[Finding] = []
    mods: list[ModInfo] = []
    nested_jar_paths: list[str] = []

    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except Exception as exc:
                findings.append(
                    Finding(
                        code=INVALID_METADATA_JSON,
                        severity=Severity.ERROR,
                        certainty=Certainty.CERTAIN,
                        message=f"Failed to decode {file_path}: {exc}",
                        evidence=[Evidence(file=jar_path or file_path, field=file_path, message=str(exc))],
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
                evidence=[Evidence(file=jar_path or file_path, field=file_path, message="Lenient JSON repaired")],
            )
        )

    if not isinstance(data, dict):
        findings.append(
            Finding(
                code=INVALID_METADATA_JSON,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Root of {file_path} must be a JSON object",
                evidence=[Evidence(file=jar_path or file_path, field=file_path, message="Root is not a JSON object")],
            )
        )
        return mods, findings, nested_jar_paths

    ql = data.get("quilt_loader", {})
    if not isinstance(ql, dict):
        findings.append(
            Finding(
                code=INVALID_METADATA_JSON,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Missing or invalid 'quilt_loader' object in {file_path}",
                evidence=[Evidence(file=jar_path or file_path, field="quilt_loader", message="quilt_loader must be an object")],
            )
        )
        return mods, findings, nested_jar_paths

    mod_id = ql.get("id")
    if not mod_id or not isinstance(mod_id, str):
        findings.append(
            Finding(
                code=INVALID_METADATA_JSON,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Missing or invalid 'quilt_loader.id' field in {file_path}",
                evidence=[
                    Evidence(
                        file=jar_path or file_path,
                        field="quilt_loader.id",
                        raw_value=str(mod_id) if mod_id is not None else None,
                        message="Mod id must be a non-empty string",
                    )
                ],
            )
        )
        return mods, findings, nested_jar_paths

    raw_version = ql.get("version")
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
                            field="quilt_loader.version",
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

    metadata = ql.get("metadata", {})
    description: str | None = None
    authors: list[str] = []
    license_str: str | None = None
    name: str | None = None

    if isinstance(metadata, dict):
        raw_name = metadata.get("name") or ql.get("name")
        if raw_name is not None and not isinstance(raw_name, str):
            name = str(raw_name)
        elif raw_name is not None:
            name = raw_name

        raw_desc = metadata.get("description") or ql.get("description")
        if raw_desc is not None:
            description = str(raw_desc)

        # Contributors object (name -> role) or list or authors
        contributors = metadata.get("contributors")
        if isinstance(contributors, dict):
            authors.extend(str(k) for k in contributors.keys())
        elif isinstance(contributors, list):
            for c in contributors:
                authors.append(str(c))
        elif isinstance(contributors, str):
            authors.append(contributors)

        raw_authors = metadata.get("authors")
        if isinstance(raw_authors, list):
            for a in raw_authors:
                if isinstance(a, str):
                    authors.append(a)
                elif isinstance(a, dict) and "name" in a:
                    authors.append(str(a["name"]))
        elif isinstance(raw_authors, str):
            authors.append(raw_authors)

        raw_lic = metadata.get("license") or ql.get("license")
        if isinstance(raw_lic, list):
            license_str = ", ".join(str(l) for l in raw_lic)
        elif isinstance(raw_lic, str):
            license_str = raw_lic
    else:
        raw_name = ql.get("name")
        if raw_name is not None:
            name = str(raw_name)

    # Provides
    provides: list[str] = []
    raw_provides = ql.get("provides")
    if isinstance(raw_provides, list):
        for p in raw_provides:
            if isinstance(p, str):
                provides.append(p)
            elif isinstance(p, dict):
                if "id" in p:
                    provides.append(str(p["id"]))
                elif "provides" in p:
                    provides.append(str(p["provides"]))

    # Dependencies: depends and breaks
    dependencies: list[Dependency] = []

    # 1. depends
    raw_deps = ql.get("depends")
    if isinstance(raw_deps, list):
        for item in raw_deps:
            dep, dep_findings = _parse_quilt_dep(
                item,
                default_kind=DependencyKind.REQUIRED,
                declared_as="depends",
                mod_id=mod_id,
                file_path=file_path,
                jar_path=jar_path,
            )
            findings.extend(dep_findings)
            if dep is not None:
                dependencies.append(dep)

    # 2. breaks
    raw_breaks = ql.get("breaks")
    if isinstance(raw_breaks, list):
        for item in raw_breaks:
            dep, dep_findings = _parse_quilt_dep(
                item,
                default_kind=DependencyKind.BREAKS,
                declared_as="breaks",
                mod_id=mod_id,
                file_path=file_path,
                jar_path=jar_path,
            )
            findings.extend(dep_findings)
            if dep is not None:
                dependencies.append(dep)

    # Nested jars
    raw_jars = ql.get("jars")
    if isinstance(raw_jars, list):
        for j_entry in raw_jars:
            if isinstance(j_entry, str):
                nested_jar_paths.append(j_entry)
            elif isinstance(j_entry, dict) and "file" in j_entry:
                nested_jar_paths.append(str(j_entry["file"]))

    mods.append(
        ModInfo(
            id=mod_id,
            name=name,
            version=version,
            loaders=[Loader.QUILT],
            provides=provides,
            dependencies=dependencies,
            jar_path=jar_path,
            metadata_file=file_path,
            description=description,
            authors=list(dict.fromkeys(authors)),
            license=license_str,
            environment=None,
            parent_jar=parent_jar,
            related_files=[],
            is_nested=is_nested,
        )
    )

    return mods, findings, nested_jar_paths
