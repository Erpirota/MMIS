"""Dependency analyzer for MMIS.

Analyzes required and optional mod dependencies, provider resolution,
offline platform constraints, and circular dependency cycles.
"""

from __future__ import annotations

from mmis.domain.findings import (
    DEP_CYCLE,
    DEP_DISABLED_PROVIDER,
    DEP_MISSING_OPTIONAL,
    DEP_MISSING_REQUIRED,
    DEP_OPTIONAL_MISMATCH,
    DEP_VERSION_MISMATCH,
    DEP_VERSION_UNDETERMINED,
    PLATFORM_DEP_UNVERIFIED,
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

PLATFORM_IDS = frozenset(
    {
        "minecraft",
        "java",
        "fabricloader",
        "fabric-loader",
        "quilt_loader",
        "forge",
        "neoforge",
        "fml",
        "javafml",
    }
)


def get_active_mods(scan: ScanResult) -> list[ModInfo]:
    """Filter ScanResult mods down to active records for the detected pack loader.

    A jar may contain metadata for multiple loaders.
    - Fabric pack: only Fabric records
    - Quilt pack: Quilt records, or Fabric records of jars with no Quilt record
    - Forge pack: Forge records
    - NeoForge pack: NeoForge records, or Forge records of jars with no NeoForge record
    - Unknown/ambiguous loader: all records
    """
    if scan.detected_loader is None:
        return list(scan.mods)

    # Group mods by jar_path
    mods_by_jar: dict[str, list[ModInfo]] = {}
    for m in scan.mods:
        mods_by_jar.setdefault(m.jar_path, []).append(m)

    active: list[ModInfo] = []
    target_loader = scan.detected_loader

    for jar_path, jar_mods in mods_by_jar.items():
        if target_loader == Loader.FABRIC:
            active.extend(m for m in jar_mods if Loader.FABRIC in m.loaders)
        elif target_loader == Loader.QUILT:
            quilt_mods = [m for m in jar_mods if Loader.QUILT in m.loaders]
            if quilt_mods:
                active.extend(quilt_mods)
            else:
                active.extend(m for m in jar_mods if Loader.FABRIC in m.loaders)
        elif target_loader == Loader.FORGE:
            active.extend(m for m in jar_mods if Loader.FORGE in m.loaders)
        elif target_loader == Loader.NEOFORGE:
            neoforge_mods = [m for m in jar_mods if Loader.NEOFORGE in m.loaders]
            if neoforge_mods:
                active.extend(neoforge_mods)
            else:
                active.extend(m for m in jar_mods if Loader.FORGE in m.loaders)

    return active


def _find_scc_cycles(adj: dict[str, set[str]]) -> list[list[str]]:
    """Find deterministic simple cycles from strongly connected components using Tarjan's algorithm."""
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    sccs: list[list[str]] = []

    nodes = sorted(adj.keys())

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlinks[v] = index
        index += 1
        stack.append(v)
        on_stack[v] = True

        for w in sorted(adj.get(v, set())):
            if w not in indices:
                strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif on_stack.get(w, False):
                lowlinks[v] = min(lowlinks[v], indices[w])

        if lowlinks[v] == indices[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(sorted(scc))

    for node in nodes:
        if node not in indices:
            strongconnect(node)

    # For each SCC, reconstruct a deterministic cycle path
    cycles: list[list[str]] = []
    for scc in sorted(sccs, key=lambda s: s[0]):
        scc_set = set(scc)
        start = scc[0]  # Lexicographically smallest

        # BFS to find shortest cycle path from start back to start within scc
        queue: list[list[str]] = [[start]]
        cycle_found: list[str] | None = None
        visited_edges: set[tuple[str, str]] = set()

        while queue:
            path = queue.pop(0)
            curr = path[-1]
            neighbors = sorted(adj.get(curr, set()) & scc_set)
            for nxt in neighbors:
                if (curr, nxt) in visited_edges:
                    continue
                visited_edges.add((curr, nxt))
                if nxt == start and len(path) > 1:
                    cycle_found = path + [start]
                    break
                if nxt not in path:
                    queue.append(path + [nxt])
            if cycle_found:
                break

        if cycle_found:
            cycles.append(cycle_found)

    return cycles


def analyze_dependencies(scan: ScanResult) -> list[Finding]:
    """Analyze dependencies for all active mods in the scan result."""
    findings: list[Finding] = []
    active_mods = get_active_mods(scan)
    jars_by_path: dict[str, JarFile] = {j.path: j for j in scan.jars}

    # Partition active mods into enabled and disabled
    enabled_mods: list[ModInfo] = []
    disabled_mods: list[ModInfo] = []
    for m in active_mods:
        jar = jars_by_path.get(m.jar_path)
        if jar is not None and jar.is_disabled:
            disabled_mods.append(m)
        else:
            enabled_mods.append(m)

    # Build provider indices: map mod_id/provides -> list of (ModInfo, version)
    enabled_providers: dict[str, list[tuple[ModInfo, str | None]]] = {}
    for m in enabled_mods:
        enabled_providers.setdefault(m.id, []).append((m, m.version))
        for p in m.provides:
            enabled_providers.setdefault(p, []).append((m, m.version))

    disabled_providers: dict[str, list[tuple[ModInfo, str | None]]] = {}
    for m in disabled_mods:
        disabled_providers.setdefault(m.id, []).append((m, m.version))
        for p in m.provides:
            disabled_providers.setdefault(p, []).append((m, m.version))

    unverified_platform_ev: list[Evidence] = []
    unverified_platform_mods: set[str] = set()

    # Evaluate dependencies for each enabled mod
    for req_mod in sorted(enabled_mods, key=lambda m: (m.id, m.jar_path)):
        for dep in req_mod.dependencies:
            # Self-dependency is ignored
            if dep.mod_id == req_mod.id:
                continue

            raw_range = dep.version_range.raw if dep.version_range is not None else "*"
            file_hint = req_mod.jar_path or req_mod.metadata_file
            dep_ev = Evidence(
                file=file_hint,
                field="dependencies",
                raw_value=f"{dep.mod_id} {raw_range}",
                message=f"'{req_mod.id}' declares dependency on '{dep.mod_id}' ({raw_range})",
            )

            # Platform dependencies
            if dep.mod_id.lower() in PLATFORM_IDS:
                if dep.mod_id.lower() == "minecraft":
                    if scan.detected_mc_version is not None:
                        if dep.version_range is not None:
                            match = dep.version_range.matches(scan.detected_mc_version)
                            if match is False:
                                # Reported as CONFLICT_MC_VERSION_MISMATCH in conflicts.py, do not duplicate here
                                continue
                            elif match is None:
                                findings.append(
                                    Finding(
                                        code=DEP_VERSION_UNDETERMINED,
                                        severity=Severity.INFO,
                                        certainty=Certainty.INSUFFICIENT_DATA,
                                        message=(
                                            f"Cannot determine if Minecraft version {scan.detected_mc_version} "
                                            f"satisfies requirement '{raw_range}' of '{req_mod.id}'"
                                        ),
                                        evidence=[dep_ev],
                                        related_mod_ids=[req_mod.id, "minecraft"],
                                        suggestion=f"Verify compatibility of '{req_mod.id}' with Minecraft {scan.detected_mc_version}.",
                                    )
                                )
                        continue
                    else:
                        unverified_platform_ev.append(dep_ev)
                        unverified_platform_mods.add(req_mod.id)
                        continue
                else:
                    unverified_platform_ev.append(dep_ev)
                    unverified_platform_mods.add(req_mod.id)
                    continue

            # Regular dependencies
            if dep.kind == DependencyKind.REQUIRED:
                if dep.mod_id not in enabled_providers:
                    if dep.mod_id in disabled_providers:
                        dis_mod, _ = disabled_providers[dep.mod_id][0]
                        dis_ev = Evidence(
                            file=dis_mod.jar_path or dis_mod.metadata_file,
                            message=f"Provider '{dep.mod_id}' is present in disabled jar '{dis_mod.jar_path}'",
                        )
                        findings.append(
                            Finding(
                                code=DEP_DISABLED_PROVIDER,
                                severity=Severity.ERROR,
                                certainty=Certainty.CERTAIN,
                                message=(
                                    f"{req_mod.id} requires {dep.mod_id} {raw_range}, "
                                    f"but provider is in a disabled jar"
                                ),
                                evidence=[dep_ev, dis_ev],
                                related_mod_ids=[req_mod.id, dep.mod_id],
                                suggestion=f"Enable jar '{dis_mod.jar_path}' to satisfy dependency '{dep.mod_id}'.",
                            )
                        )
                    else:
                        findings.append(
                            Finding(
                                code=DEP_MISSING_REQUIRED,
                                severity=Severity.ERROR,
                                certainty=Certainty.CERTAIN,
                                message=f"{req_mod.id} requires {dep.mod_id} {raw_range}, which is missing",
                                evidence=[dep_ev],
                                related_mod_ids=[req_mod.id, dep.mod_id],
                                suggestion=f"Install mod '{dep.mod_id}' matching version range '{raw_range}'.",
                            )
                        )
                else:
                    # Target is present in enabled providers
                    if dep.version_range is not None:
                        providers = enabled_providers[dep.mod_id]
                        satisfied = False
                        has_none = False
                        mismatches: list[tuple[ModInfo, str]] = []

                        for p_mod, p_ver in providers:
                            if p_ver is None:
                                has_none = True
                                continue
                            res = dep.version_range.matches(p_ver)
                            if res is True:
                                satisfied = True
                                break
                            elif res is None:
                                has_none = True
                            else:
                                mismatches.append((p_mod, p_ver))

                        if not satisfied:
                            if mismatches and not has_none:
                                p_mod, p_ver = mismatches[0]
                                p_ev = Evidence(
                                    file=p_mod.jar_path or p_mod.metadata_file,
                                    field="version",
                                    raw_value=p_ver,
                                    message=f"Installed version of '{dep.mod_id}' is '{p_ver}'",
                                )
                                findings.append(
                                    Finding(
                                        code=DEP_VERSION_MISMATCH,
                                        severity=Severity.ERROR,
                                        certainty=Certainty.CERTAIN,
                                        message=(
                                            f"{req_mod.id} requires {dep.mod_id} {raw_range}, "
                                            f"but {dep.mod_id} {p_ver} is installed"
                                        ),
                                        evidence=[dep_ev, p_ev],
                                        related_mod_ids=[req_mod.id, dep.mod_id],
                                        suggestion=f"Update or replace '{dep.mod_id}' to satisfy requirement '{raw_range}'.",
                                    )
                                )
                            elif has_none:
                                p_mod, p_ver = providers[0]
                                p_ev = Evidence(
                                    file=p_mod.jar_path or p_mod.metadata_file,
                                    field="version",
                                    raw_value=str(p_ver),
                                    message=f"Installed version of '{dep.mod_id}' is '{p_ver}'",
                                )
                                findings.append(
                                    Finding(
                                        code=DEP_VERSION_UNDETERMINED,
                                        severity=Severity.INFO,
                                        certainty=Certainty.INSUFFICIENT_DATA,
                                        message=(
                                            f"Cannot determine if {dep.mod_id} version {p_ver} "
                                            f"satisfies requirement '{raw_range}' of {req_mod.id}"
                                        ),
                                        evidence=[dep_ev, p_ev],
                                        related_mod_ids=[req_mod.id, dep.mod_id],
                                        suggestion=f"Manually verify compatibility between '{req_mod.id}' and '{dep.mod_id}'.",
                                    )
                                )

            elif dep.kind == DependencyKind.OPTIONAL:
                if dep.mod_id not in enabled_providers:
                    findings.append(
                        Finding(
                            code=DEP_MISSING_OPTIONAL,
                            severity=Severity.INFO,
                            certainty=Certainty.CERTAIN,
                            message=f"{req_mod.id} optionally recommends {dep.mod_id} {raw_range}, which is not installed",
                            evidence=[dep_ev],
                            related_mod_ids=[req_mod.id, dep.mod_id],
                            suggestion=f"Optionally install '{dep.mod_id}' to enable additional features.",
                        )
                    )
                else:
                    if dep.version_range is not None:
                        providers = enabled_providers[dep.mod_id]
                        satisfied = False
                        mismatches = []
                        for p_mod, p_ver in providers:
                            if p_ver is None:
                                continue
                            res = dep.version_range.matches(p_ver)
                            if res is True:
                                satisfied = True
                                break
                            elif res is False:
                                mismatches.append((p_mod, p_ver))

                        if not satisfied and mismatches:
                            p_mod, p_ver = mismatches[0]
                            p_ev = Evidence(
                                file=p_mod.jar_path or p_mod.metadata_file,
                                field="version",
                                raw_value=p_ver,
                                message=f"Installed version of '{dep.mod_id}' is '{p_ver}'",
                            )
                            findings.append(
                                Finding(
                                    code=DEP_OPTIONAL_MISMATCH,
                                    severity=Severity.WARNING,
                                    certainty=Certainty.CERTAIN,
                                    message=(
                                        f"{req_mod.id} optionally recommends {dep.mod_id} {raw_range}, "
                                        f"but {dep.mod_id} {p_ver} is installed"
                                    ),
                                    evidence=[dep_ev, p_ev],
                                    related_mod_ids=[req_mod.id, dep.mod_id],
                                    suggestion=f"Update '{dep.mod_id}' to satisfy '{raw_range}' for recommended features.",
                                )
                            )

    # Aggregated platform dependency finding (at most one per pack)
    if unverified_platform_ev:
        findings.append(
            Finding(
                code=PLATFORM_DEP_UNVERIFIED,
                severity=Severity.INFO,
                certainty=Certainty.INSUFFICIENT_DATA,
                message=f"{len(unverified_platform_ev)} platform dependencies cannot be verified offline",
                evidence=unverified_platform_ev,
                related_mod_ids=sorted(unverified_platform_mods),
                suggestion="Ensure runtime environment (Java, loader) satisfies declared platform requirements.",
            )
        )

    # Cycle detection via Tarjan's SCC over enabled active mods with REQUIRED dependencies
    mod_map: dict[str, ModInfo] = {m.id: m for m in enabled_mods}
    adj: dict[str, set[str]] = {m.id: set() for m in enabled_mods}

    for m in enabled_mods:
        for dep in m.dependencies:
            if dep.kind == DependencyKind.REQUIRED and dep.mod_id != m.id:
                if dep.mod_id in mod_map:
                    adj[m.id].add(dep.mod_id)
                elif dep.mod_id in enabled_providers:
                    for prov_mod, _ in enabled_providers[dep.mod_id]:
                        if prov_mod.id != m.id and prov_mod.id in mod_map:
                            adj[m.id].add(prov_mod.id)

    cycles = _find_scc_cycles(adj)
    for cycle in cycles:
        cycle_nodes = cycle[:-1]
        cycle_str = " -> ".join(cycle)
        ev_list = [
            Evidence(
                file=mod_map[nid].jar_path or mod_map[nid].metadata_file,
                field="dependencies",
                message=f"Mod '{nid}' is part of circular dependency",
            )
            for nid in cycle_nodes
            if nid in mod_map
        ]
        findings.append(
            Finding(
                code=DEP_CYCLE,
                severity=Severity.WARNING,
                certainty=Certainty.CERTAIN,
                message=f"Circular dependency detected: {cycle_str}. Impact depends on loader.",
                evidence=ev_list,
                related_mod_ids=sorted(cycle_nodes),
                suggestion="Check if mod loader supports circular dependencies, or update mods to resolve cycle.",
            )
        )

    return findings
