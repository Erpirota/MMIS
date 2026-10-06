"""Conflict analyzer for MMIS.

Detects duplicate top-level mods, declared breaks/conflicts, loader mismatches,
Minecraft version incompatibilities, and multiple providers.
"""

from __future__ import annotations

from mmis.analysis.dependencies import get_active_mods
from mmis.domain.findings import (
    CONFLICT_BREAKS_DECLARED,
    CONFLICT_DUPLICATE_MOD,
    CONFLICT_LOADER_MISMATCH,
    CONFLICT_MC_VERSION_MISMATCH,
    CONFLICT_MULTIPLE_PROVIDERS,
    CONFLICT_SOFT_DISCOURAGED,
    CONFLICT_UNANALYZABLE_JARS,
    CORRUPT_JAR,
    INVALID_METADATA_JSON,
    INVALID_METADATA_TOML,
    NO_METADATA,
    NOT_A_ZIP,
)
from mmis.domain.models import (
    Certainty,
    DependencyKind,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)

UNANALYZABLE_CODES = frozenset(
    {
        NO_METADATA,
        CORRUPT_JAR,
        NOT_A_ZIP,
        INVALID_METADATA_JSON,
        INVALID_METADATA_TOML,
    }
)


def analyze_conflicts(scan: ScanResult) -> list[Finding]:
    """Analyze conflicts across mods in the scan result."""
    findings: list[Finding] = []
    jars_by_path: dict[str, JarFile] = {j.path: j for j in scan.jars}
    active_mods = get_active_mods(scan)

    # Filter enabled active mods
    enabled_mods: list[ModInfo] = []
    for m in active_mods:
        jar = jars_by_path.get(m.jar_path)
        if jar is None or not jar.is_disabled:
            enabled_mods.append(m)

    # 1. CERTAIN: CONFLICT_DUPLICATE_MOD
    # Same mod id in two or more enabled TOP-LEVEL jars
    # Nested jars are normal jar-in-jar behavior (no finding)
    # The same id coming twice from the SAME jar is not a duplicate
    top_level_mods_by_id: dict[str, dict[str, ModInfo]] = {}
    for m in enabled_mods:
        if not m.is_nested:
            top_level_mods_by_id.setdefault(m.id, {})[m.jar_path] = m

    for mod_id, jar_map in sorted(top_level_mods_by_id.items()):
        if len(jar_map) > 1:
            ev_list = [
                Evidence(
                    file=j_path,
                    field="id",
                    raw_value=mod_id,
                    message=f"Duplicate mod '{mod_id}' found in top-level jar '{j_path}'",
                )
                for j_path in sorted(jar_map.keys())
            ]
            findings.append(
                Finding(
                    code=CONFLICT_DUPLICATE_MOD,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=f"Duplicate mod ID '{mod_id}' found in {len(jar_map)} enabled top-level jars",
                    evidence=ev_list,
                    related_mod_ids=[mod_id],
                    suggestion=f"Remove duplicate jar files for mod '{mod_id}'.",
                )
            )

    # Build provider map for breaks/conflicts lookup
    enabled_providers: dict[str, list[tuple[ModInfo, str | None]]] = {}
    for m in enabled_mods:
        enabled_providers.setdefault(m.id, []).append((m, m.version))
        for p in m.provides:
            enabled_providers.setdefault(p, []).append((m, m.version))

    # 2. CERTAIN & POSSIBLE: BREAKS and CONFLICTS
    for m in sorted(enabled_mods, key=lambda x: (x.id, x.jar_path)):
        for dep in m.dependencies:
            if dep.mod_id == m.id:
                continue

            raw_range = dep.version_range.raw if dep.version_range is not None else "*"
            file_hint = m.jar_path or m.metadata_file

            if dep.kind == DependencyKind.BREAKS:
                if dep.mod_id in enabled_providers:
                    providers = enabled_providers[dep.mod_id]
                    if dep.version_range is None:
                        # Matches unconditionally -> CERTAIN
                        target_mod, target_ver = providers[0]
                        ev1 = Evidence(
                            file=file_hint,
                            field="dependencies",
                            raw_value=f"{dep.mod_id} {raw_range}",
                            message=f"'{m.id}' declares breaking conflict with '{dep.mod_id}'",
                        )
                        ev2 = Evidence(
                            file=target_mod.jar_path or target_mod.metadata_file,
                            message=f"Conflicting mod '{dep.mod_id}' is present",
                        )
                        findings.append(
                            Finding(
                                code=CONFLICT_BREAKS_DECLARED,
                                severity=Severity.ERROR,
                                certainty=Certainty.CERTAIN,
                                message=f"'{m.id}' breaks installed mod '{dep.mod_id}'",
                                evidence=[ev1, ev2],
                                related_mod_ids=[m.id, dep.mod_id],
                                suggestion=f"Uninstall '{dep.mod_id}' or replace with a compatible version.",
                            )
                        )
                    else:
                        # Version range is specified
                        matches_true = False
                        matches_none = False
                        matched_provider: tuple[ModInfo, str | None] | None = None

                        for p_mod, p_ver in providers:
                            if p_ver is None:
                                matches_none = True
                                continue
                            res = dep.version_range.matches(p_ver)
                            if res is True:
                                matches_true = True
                                matched_provider = (p_mod, p_ver)
                                break
                            elif res is None:
                                matches_none = True

                        if matches_true and matched_provider:
                            p_mod, p_ver = matched_provider
                            ev1 = Evidence(
                                file=file_hint,
                                field="dependencies",
                                raw_value=f"{dep.mod_id} {raw_range}",
                                message=f"'{m.id}' breaks '{dep.mod_id}' {raw_range}",
                            )
                            ev2 = Evidence(
                                file=p_mod.jar_path or p_mod.metadata_file,
                                field="version",
                                raw_value=p_ver,
                                message=f"Installed version of '{dep.mod_id}' is '{p_ver}'",
                            )
                            findings.append(
                                Finding(
                                    code=CONFLICT_BREAKS_DECLARED,
                                    severity=Severity.ERROR,
                                    certainty=Certainty.CERTAIN,
                                    message=(
                                        f"'{m.id}' breaks '{dep.mod_id}' {raw_range}, "
                                        f"and '{dep.mod_id}' {p_ver} is installed"
                                    ),
                                    evidence=[ev1, ev2],
                                    related_mod_ids=[m.id, dep.mod_id],
                                    suggestion=f"Uninstall or downgrade/upgrade '{dep.mod_id}' to a non-conflicting version.",
                                )
                            )
                        elif matches_none and not matches_true:
                            p_mod, p_ver = providers[0]
                            ev1 = Evidence(
                                file=file_hint,
                                field="dependencies",
                                raw_value=f"{dep.mod_id} {raw_range}",
                                message=f"'{m.id}' declared breaks on '{dep.mod_id}' {raw_range}",
                            )
                            ev2 = Evidence(
                                file=p_mod.jar_path or p_mod.metadata_file,
                                field="version",
                                raw_value=str(p_ver),
                                message=f"Installed version of '{dep.mod_id}' is '{p_ver}'",
                            )
                            findings.append(
                                Finding(
                                    code=CONFLICT_BREAKS_DECLARED,
                                    severity=Severity.WARNING,
                                    certainty=Certainty.POSSIBLE,
                                    message=(
                                        f"'{m.id}' declares breaks on '{dep.mod_id}' {raw_range}, "
                                        f"but version check against '{p_ver}' is undetermined"
                                    ),
                                    evidence=[ev1, ev2],
                                    related_mod_ids=[m.id, dep.mod_id],
                                    suggestion=f"Manually verify whether installed '{dep.mod_id}' conflicts with '{m.id}'.",
                                )
                            )

            elif dep.kind == DependencyKind.CONFLICTS:
                if dep.mod_id in enabled_providers:
                    p_mod, _ = enabled_providers[dep.mod_id][0]
                    ev1 = Evidence(
                        file=file_hint,
                        field="dependencies",
                        raw_value=f"{dep.mod_id} {raw_range}",
                        message=f"'{m.id}' declares soft conflict with '{dep.mod_id}'",
                    )
                    ev2 = Evidence(
                        file=p_mod.jar_path or p_mod.metadata_file,
                        message=f"Mod '{dep.mod_id}' is present",
                    )
                    findings.append(
                        Finding(
                            code=CONFLICT_SOFT_DISCOURAGED,
                            severity=Severity.WARNING,
                            certainty=Certainty.POSSIBLE,
                            message=f"'{m.id}' declares soft conflict/discouraged with '{dep.mod_id}'",
                            evidence=[ev1, ev2],
                            related_mod_ids=[m.id, dep.mod_id],
                            suggestion=f"Review compatibility between '{m.id}' and '{dep.mod_id}'.",
                        )
                    )

    # 3. CERTAIN & POSSIBLE: LOADER MISMATCH
    # Only evaluated when pack loader is known
    if scan.detected_loader is not None:
        target_loader = scan.detected_loader

        # Group all scan mods by jar_path
        all_mods_by_jar: dict[str, list[ModInfo]] = {}
        for m in scan.mods:
            all_mods_by_jar.setdefault(m.jar_path, []).append(m)

        for jar_path, jar_mods in sorted(all_mods_by_jar.items()):
            jar = jars_by_path.get(jar_path)
            if jar is not None and jar.is_disabled:
                continue

            has_active = False
            if target_loader == Loader.FABRIC:
                has_active = any(Loader.FABRIC in m.loaders for m in jar_mods)
            elif target_loader == Loader.QUILT:
                has_active = any(
                    (Loader.QUILT in m.loaders or Loader.FABRIC in m.loaders) for m in jar_mods
                )
            elif target_loader == Loader.FORGE:
                has_active = any(Loader.FORGE in m.loaders for m in jar_mods)
            elif target_loader == Loader.NEOFORGE:
                has_active = any(
                    (Loader.NEOFORGE in m.loaders or Loader.FORGE in m.loaders) for m in jar_mods
                )

            if has_active:
                # Jar has active record!
                # Check for Forge-only mod in a NeoForge pack (POSSIBLE conflict)
                if target_loader == Loader.NEOFORGE:
                    has_neoforge = any(Loader.NEOFORGE in m.loaders for m in jar_mods)
                    if not has_neoforge and any(Loader.FORGE in m.loaders for m in jar_mods):
                        forge_mod = next(m for m in jar_mods if Loader.FORGE in m.loaders)
                        findings.append(
                            Finding(
                                code=CONFLICT_LOADER_MISMATCH,
                                severity=Severity.WARNING,
                                certainty=Certainty.POSSIBLE,
                                message=(
                                    f"Forge-only mod '{forge_mod.id}' in NeoForge pack "
                                    "may have compatibility issues"
                                ),
                                evidence=[
                                    Evidence(
                                        file=forge_mod.jar_path or forge_mod.metadata_file,
                                        message="Forge metadata (mods.toml) without native NeoForge metadata",
                                    )
                                ],
                                related_mod_ids=[forge_mod.id],
                                suggestion=f"Verify that Forge mod '{forge_mod.id}' works on NeoForge or obtain a native build.",
                            )
                        )
            else:
                # Jar has NO active records for the pack loader -> CERTAIN loader mismatch
                for m in jar_mods:
                    loaders_str = ", ".join(l.value for l in m.loaders) or "unknown"
                    findings.append(
                        Finding(
                            code=CONFLICT_LOADER_MISMATCH,
                            severity=Severity.ERROR,
                            certainty=Certainty.CERTAIN,
                            message=(
                                f"Mod '{m.id}' targets loader '{loaders_str}', "
                                f"incompatible with pack loader '{target_loader.value}'"
                            ),
                            evidence=[
                                Evidence(
                                    file=m.jar_path or m.metadata_file,
                                    message=f"Mod loaders: {loaders_str}",
                                )
                            ],
                            related_mod_ids=[m.id],
                            suggestion=f"Replace mod '{m.id}' with a version built for '{target_loader.value}'.",
                        )
                    )

    # 4. CERTAIN: CONFLICT_MC_VERSION_MISMATCH
    if scan.detected_mc_version is not None:
        for m in sorted(enabled_mods, key=lambda x: (x.id, x.jar_path)):
            for dep in m.dependencies:
                if dep.mod_id.lower() == "minecraft" and dep.version_range is not None:
                    if dep.version_range.matches(scan.detected_mc_version) is False:
                        raw_range = dep.version_range.raw
                        findings.append(
                            Finding(
                                code=CONFLICT_MC_VERSION_MISMATCH,
                                severity=Severity.ERROR,
                                certainty=Certainty.CERTAIN,
                                message=(
                                    f"Mod '{m.id}' requires Minecraft {raw_range}, "
                                    f"but pack is running {scan.detected_mc_version}"
                                ),
                                evidence=[
                                    Evidence(
                                        file=m.jar_path or m.metadata_file,
                                        field="dependencies",
                                        raw_value=f"minecraft {raw_range}",
                                        message=f"Minecraft constraint: {raw_range}",
                                    )
                                ],
                                related_mod_ids=[m.id, "minecraft"],
                                suggestion=f"Update mod '{m.id}' to support Minecraft {scan.detected_mc_version}.",
                            )
                        )

    # 5. POSSIBLE: CONFLICT_MULTIPLE_PROVIDERS
    # Two different top-level mods providing the same id, or providing the id of another present mod
    top_level_enabled_mods = [m for m in enabled_mods if not m.is_nested]
    providers_by_id: dict[str, set[str]] = {}
    mod_by_id_map: dict[str, ModInfo] = {}

    for m in top_level_enabled_mods:
        mod_by_id_map[m.id] = m
        for p in m.provides:
            providers_by_id.setdefault(p, set()).add(m.id)

    # Case A: Two different top-level mods provide the same provided ID
    for prov_id, provider_mod_ids in sorted(providers_by_id.items()):
        if len(provider_mod_ids) > 1:
            sorted_prov_mods = sorted(provider_mod_ids)
            ev_list = [
                Evidence(
                    file=mod_by_id_map[mid].jar_path or mod_by_id_map[mid].metadata_file,
                    field="provides",
                    raw_value=prov_id,
                    message=f"Mod '{mid}' declares providing '{prov_id}'",
                )
                for mid in sorted_prov_mods
                if mid in mod_by_id_map
            ]
            findings.append(
                Finding(
                    code=CONFLICT_MULTIPLE_PROVIDERS,
                    severity=Severity.WARNING,
                    certainty=Certainty.POSSIBLE,
                    message=f"Multiple top-level mods ({', '.join(sorted_prov_mods)}) provide '{prov_id}'",
                    evidence=ev_list,
                    related_mod_ids=sorted_prov_mods,
                    suggestion=f"Verify that multiple providers of '{prov_id}' do not conflict.",
                )
            )
        elif len(provider_mod_ids) == 1:
            # Case B: A top-level mod provides the id of another present top-level mod
            prov_mod_id = next(iter(provider_mod_ids))
            if prov_id in mod_by_id_map and prov_id != prov_mod_id:
                other_mod = mod_by_id_map[prov_id]
                ev1 = Evidence(
                    file=mod_by_id_map[prov_mod_id].jar_path or mod_by_id_map[prov_mod_id].metadata_file,
                    field="provides",
                    raw_value=prov_id,
                    message=f"Mod '{prov_mod_id}' provides '{prov_id}'",
                )
                ev2 = Evidence(
                    file=other_mod.jar_path or other_mod.metadata_file,
                    field="id",
                    raw_value=prov_id,
                    message=f"Mod '{prov_id}' is installed as a standalone mod",
                )
                findings.append(
                    Finding(
                        code=CONFLICT_MULTIPLE_PROVIDERS,
                        severity=Severity.WARNING,
                        certainty=Certainty.POSSIBLE,
                        message=f"Mod '{prov_mod_id}' provides '{prov_id}', which is also installed as a standalone mod",
                        evidence=[ev1, ev2],
                        related_mod_ids=sorted([prov_mod_id, prov_id]),
                        suggestion=f"Ensure '{prov_mod_id}' and '{prov_id}' do not conflict.",
                    )
                )

    # 6. INSUFFICIENT_DATA: CONFLICT_UNANALYZABLE_JARS
    # One aggregated finding listing jars that could not be analyzed
    unanalyzable_ev: list[Evidence] = []
    seen_files: set[str] = set()
    for f in scan.findings:
        if f.code in UNANALYZABLE_CODES:
            for ev in f.evidence:
                if ev.file and ev.file not in seen_files:
                    seen_files.add(ev.file)
                    unanalyzable_ev.append(
                        Evidence(
                            file=ev.file,
                            message=f"Unanalyzable jar ({f.code}): {f.message}",
                        )
                    )

    if unanalyzable_ev:
        findings.append(
            Finding(
                code=CONFLICT_UNANALYZABLE_JARS,
                severity=Severity.INFO,
                certainty=Certainty.INSUFFICIENT_DATA,
                message=f"{len(unanalyzable_ev)} jar file(s) could not be analyzed due to missing or invalid metadata",
                evidence=unanalyzable_ev,
                related_mod_ids=[],
                suggestion="Inspect unanalyzable jars to confirm valid Minecraft mod packaging.",
            )
        )

    return findings
