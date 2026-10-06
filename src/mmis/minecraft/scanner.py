"""Modpack scanner orchestrator.

Discovers jars, inspects nested jars, resolves dependencies, matches configs,
and derives pack-level loader and Minecraft version.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from mmis.domain.findings import (
    AMBIGUOUS_LOADER,
    AMBIGUOUS_MC_VERSION,
    CORRUPT_JAR,
    UNKNOWN_LOADER,
)
from mmis.domain.models import (
    Certainty,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)
from mmis.domain.versions import VersionRange
from mmis.minecraft.jar import read_jar
from mmis.minecraft.pack_manifests import detect_pack_manifest


def _to_rel_forward(path: str, root_dir: str) -> str:
    """Convert absolute or relative path to a relative path from root_dir with forward slashes."""
    rel = os.path.relpath(path, root_dir)
    return rel.replace("\\", "/")


def _find_related_config_files(config_dir: str, root_dir: str, mod_ids: list[str]) -> tuple[dict[str, list[str]], list[Finding]]:
    """Find files under config_dir associated with mod_ids via name-based heuristic."""
    mod_configs: dict[str, list[str]] = {mid: [] for mid in mod_ids}
    findings: list[Finding] = []
    if not os.path.isdir(config_dir):
        return mod_configs, findings

    lower_mod_ids = {mid.lower(): mid for mid in mod_ids}

    try:
        entries = os.listdir(config_dir)
    except OSError as exc:
        findings.append(
            Finding(
                code=CORRUPT_JAR,
                severity=Severity.WARNING,
                certainty=Certainty.POSSIBLE,
                message=f"I/O error reading config directory: {exc}",
                evidence=[Evidence(file=_to_rel_forward(config_dir, root_dir), message=str(exc))],
            )
        )
        return mod_configs, findings

    for entry in entries:
        full_entry_path = os.path.join(config_dir, entry)
        entry_lower = entry.lower()

        # Find matching mods whose ID is a prefix of this entry
        matched_mids = [
            original_mid
            for low_id, original_mid in lower_mod_ids.items()
            if entry_lower == low_id
            or entry_lower.startswith(f"{low_id}.")
            or entry_lower.startswith(f"{low_id}-")
            or entry_lower.startswith(f"{low_id}_")
        ]

        if not matched_mids:
            continue

        if os.path.isfile(full_entry_path):
            rel_file = _to_rel_forward(full_entry_path, root_dir)
            for mid in matched_mids:
                mod_configs[mid].append(rel_file)
        elif os.path.isdir(full_entry_path):
            try:
                for root, _, files in os.walk(full_entry_path):
                    for f in files:
                        sub_file = os.path.join(root, f)
                        rel_file = _to_rel_forward(sub_file, root_dir)
                        for mid in matched_mids:
                            mod_configs[mid].append(rel_file)
            except OSError as exc:
                findings.append(
                    Finding(
                        code=CORRUPT_JAR,
                        severity=Severity.WARNING,
                        certainty=Certainty.POSSIBLE,
                        message=f"I/O error walking config directory {entry}: {exc}",
                        evidence=[Evidence(file=_to_rel_forward(full_entry_path, root_dir), message=str(exc))],
                    )
                )

    return mod_configs, findings


def scan(target_dir: str) -> ScanResult:
    """Scan a Minecraft modpack directory and return a structured ScanResult."""
    abs_target = os.path.abspath(target_dir)

    # 1. Determine scan target: <dir>/mods if exists, else <dir>
    mods_subdir = os.path.join(abs_target, "mods")
    if os.path.isdir(mods_subdir):
        jars_dir = mods_subdir
        mods_dir_rel = "mods"
    else:
        jars_dir = abs_target
        mods_dir_rel = ""

    all_jars: list[JarFile] = []
    all_mods: list[ModInfo] = []
    all_findings: list[Finding] = []

    # 2. Check pack-level manifests in abs_target
    manifest_loader, manifest_mc_ver, manifest_findings = detect_pack_manifest(abs_target)
    all_findings.extend(manifest_findings)

    # 3. Discover top-level jar files
    top_level_files: list[str] = []
    if os.path.isdir(jars_dir):
        try:
            for entry in os.listdir(jars_dir):
                lower = entry.lower()
                if lower.endswith(".jar") or lower.endswith(".jar.disabled"):
                    full_p = os.path.join(jars_dir, entry)
                    if os.path.isfile(full_p):
                        top_level_files.append(full_p)
        except Exception as exc:
            all_findings.append(
                Finding(
                    code=CORRUPT_JAR,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Failed to list directory {jars_dir}: {exc}",
                    evidence=[Evidence(file=_to_rel_forward(jars_dir, abs_target), message=str(exc))],
                )
            )

    top_level_files.sort()

    # 4. Process each top-level jar and nested jars
    nested_queue: list[tuple[str, bytes, str, int]] = []  # (display_path, data, parent_path, depth)

    for jar_path in top_level_files:
        is_disabled = jar_path.lower().endswith(".jar.disabled")
        display_path = _to_rel_forward(jar_path, abs_target)

        j_file, mods, findings, nested_items = read_jar(
            data_or_path=jar_path,
            display_path=display_path,
            is_disabled=is_disabled,
            parent_path=None,
            depth=0,
        )
        all_jars.append(j_file)
        all_mods.extend(mods)
        all_findings.extend(findings)

        for n_path, n_bytes in nested_items:
            nested_queue.append((n_path, n_bytes, display_path, 1))

    # Process nested jars in memory
    while nested_queue:
        n_display_path, n_bytes, parent_p, depth = nested_queue.pop(0)
        j_file, mods, findings, next_nested = read_jar(
            data_or_path=n_bytes,
            display_path=n_display_path,
            is_disabled=False,
            parent_path=parent_p,
            depth=depth,
        )
        all_jars.append(j_file)
        all_mods.extend(mods)
        all_findings.extend(findings)

        for next_path, next_bytes in next_nested:
            nested_queue.append((next_path, next_bytes, n_display_path, depth + 1))

    # 5. Connect related configuration files
    config_dir = os.path.join(abs_target, "config")
    mod_ids = [m.id for m in all_mods]
    mod_configs, cfg_findings = _find_related_config_files(config_dir, abs_target, mod_ids)
    all_findings.extend(cfg_findings)

    updated_mods: list[ModInfo] = []
    for m in all_mods:
        cfg_files = mod_configs.get(m.id, [])
        if cfg_files:
            # ModInfo is frozen dataclass, construct new with related_files
            updated_mods.append(
                ModInfo(
                    id=m.id,
                    name=m.name,
                    version=m.version,
                    loaders=m.loaders,
                    provides=m.provides,
                    dependencies=m.dependencies,
                    jar_path=m.jar_path,
                    metadata_file=m.metadata_file,
                    description=m.description,
                    authors=m.authors,
                    license=m.license,
                    environment=m.environment,
                    parent_jar=m.parent_jar,
                    related_files=sorted(cfg_files),
                    is_nested=m.is_nested,
                )
            )
        else:
            updated_mods.append(m)
    all_mods = updated_mods

    # 6. Loader Derivation
    detected_loader: Loader | None = manifest_loader
    if detected_loader is None:
        # Rule 5b:
        # Compatible loaders for each enabled top-level jar that has metadata
        jar_to_mods: dict[str, list[ModInfo]] = {}
        for m in all_mods:
            if not m.is_nested:
                jar_to_mods.setdefault(m.jar_path, []).append(m)

        enabled_jars_with_meta = [
            j for j in all_jars if not j.is_disabled and not j.is_nested and len(jar_to_mods.get(j.path, [])) > 0
        ]

        if not enabled_jars_with_meta:
            detected_loader = None
            all_findings.append(
                Finding(
                    code=UNKNOWN_LOADER,
                    severity=Severity.INFO,
                    certainty=Certainty.INSUFFICIENT_DATA,
                    message="No enabled jars with mod metadata found to determine mod loader",
                    evidence=[Evidence(file=_to_rel_forward(jars_dir, abs_target), message="No metadata found in jars")],
                )
            )
        else:
            # For each jar, find set of compatible loaders
            jar_compatibilities: list[set[Loader]] = []
            has_quilt_only_jar = False
            format_counts: Counter[str] = Counter()

            for j in enabled_jars_with_meta:
                mods_in_jar = jar_to_mods.get(j.path, [])
                jar_loaders: set[Loader] = set()
                for m in mods_in_jar:
                    for l in m.loaders:
                        jar_loaders.add(l)

                compat: set[Loader] = set()
                # A loader L is compatible with a jar if jar has L metadata, or L=quilt and jar has Fabric metadata
                if Loader.FABRIC in jar_loaders:
                    format_counts["fabric"] += 1
                    compat.add(Loader.FABRIC)
                    compat.add(Loader.QUILT)
                if Loader.QUILT in jar_loaders:
                    format_counts["quilt"] += 1
                    compat.add(Loader.QUILT)
                    if Loader.FABRIC not in jar_loaders:
                        has_quilt_only_jar = True
                if Loader.FORGE in jar_loaders:
                    format_counts["forge"] += 1
                    compat.add(Loader.FORGE)
                if Loader.NEOFORGE in jar_loaders:
                    format_counts["neoforge"] += 1
                    compat.add(Loader.NEOFORGE)

                jar_compatibilities.append(compat)

            # Intersection across all enabled jars with metadata
            candidates = set.intersection(*jar_compatibilities) if jar_compatibilities else set()

            if len(candidates) == 1:
                detected_loader = next(iter(candidates))
            elif candidates == {Loader.FABRIC, Loader.QUILT} and not has_quilt_only_jar:
                detected_loader = Loader.FABRIC
            else:
                detected_loader = None
                counts_desc = ", ".join(f"{k}: {v}" for k, v in sorted(format_counts.items()))
                all_findings.append(
                    Finding(
                        code=AMBIGUOUS_LOADER,
                        severity=Severity.WARNING,
                        certainty=Certainty.POSSIBLE,
                        message=f"Ambiguous or conflicting mod loaders across jars ({counts_desc or 'no compatible loader'})",
                        evidence=[
                            Evidence(
                                file=j.path,
                                message=f"Compatible loaders: {sorted(l.value for l in c)}",
                            )
                            for j, c in zip(enabled_jars_with_meta, jar_compatibilities)
                        ],
                    )
                )

    # 7. Minecraft Version Derivation
    detected_mc_version: str | None = manifest_mc_ver
    mc_constraints: dict[str, int] = {}

    # Collect minecraft constraints from all non-disabled mods
    disabled_jar_paths = {j.path for j in all_jars if j.is_disabled}
    all_mc_ranges: list[VersionRange] = []

    for m in all_mods:
        if m.jar_path in disabled_jar_paths:
            continue
        for dep in m.dependencies:
            if dep.mod_id.lower() == "minecraft":
                if dep.version_range is not None:
                    raw_str = dep.version_range.raw
                    mc_constraints[raw_str] = mc_constraints.get(raw_str, 0) + 1
                    if dep.version_range.is_valid:
                        all_mc_ranges.append(dep.version_range)

    if detected_mc_version is None and mc_constraints:
        # Rule 5c: set detected_mc_version only if the minecraft constraints pin one single exact version
        exact_versions: set[str] = set()
        for vr in all_mc_ranges:
            exact = vr.extract_exact_version()
            if exact:
                exact_versions.add(exact)

        if len(exact_versions) == 1:
            cand = next(iter(exact_versions))
            # Verify candidate matches all valid ranges
            all_satisfied = True
            for vr in all_mc_ranges:
                res = vr.matches(cand)
                if res is False:
                    all_satisfied = False
                    break
            if all_satisfied:
                detected_mc_version = cand
            else:
                detected_mc_version = None
                all_findings.append(
                    Finding(
                        code=AMBIGUOUS_MC_VERSION,
                        severity=Severity.WARNING,
                        certainty=Certainty.POSSIBLE,
                        message=f"Candidate Minecraft version '{cand}' does not satisfy all declared constraints",
                        evidence=[
                            Evidence(
                                file=_to_rel_forward(jars_dir, abs_target),
                                raw_value=cand,
                                message="Constraint conflict",
                            )
                        ],
                    )
                )
        elif len(exact_versions) > 1:
            detected_mc_version = None
            all_findings.append(
                Finding(
                    code=AMBIGUOUS_MC_VERSION,
                    severity=Severity.WARNING,
                    certainty=Certainty.POSSIBLE,
                    message=f"Multiple conflicting exact Minecraft versions declared: {sorted(exact_versions)}",
                    evidence=[
                        Evidence(
                            file=_to_rel_forward(jars_dir, abs_target),
                            raw_value=", ".join(sorted(exact_versions)),
                            message="Multiple exact versions declared",
                        )
                    ],
                )
            )
        else:
            # No exact version pinned
            detected_mc_version = None

    return ScanResult(
        scanned_path=_to_rel_forward(abs_target, abs_target) or ".",
        mods_dir=mods_dir_rel,
        jars=all_jars,
        mods=all_mods,
        findings=all_findings,
        detected_loader=detected_loader,
        detected_mc_version=detected_mc_version,
        mc_constraints=mc_constraints,
    )
