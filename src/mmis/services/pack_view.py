"""Presentation view model and pure filtering/sorting for mod lists."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Sequence

from mmis.analysis.engine import AnalysisReport
from mmis.domain.findings import AGGREGATE_CODES
from mmis.domain.models import Dependency, Finding, Loader, ModInfo, ScanResult, Severity
from mmis.domain.profiles import ScanSummary

# Pack-level codes that should never attach to individual mod rows or drawers
PACK_LEVEL_CODES = frozenset(
    AGGREGATE_CODES | {
        "AMBIGUOUS_MC_VERSION",
        "CONFLICTING_MANIFEST",
        "PACK_MANIFEST_DETECTED",
        "INVALID_PACK_MANIFEST",
    }
)


@dataclass(frozen=True)
class ModRow:
    """Pure presentation model for a single mod displayed in the UI."""

    mod_id: str
    name: str
    version: str
    loader: str
    jar_path: str
    jar_name: str
    is_disabled: bool
    is_nested: bool
    status: str  # "ok", "warning", "error", "disabled"
    issue_count: int
    error_count: int
    warning_count: int
    mod_info: ModInfo
    other_formats: list[ModInfo] = field(default_factory=list)
    related_findings: list[Finding] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    description: str = "Unknown"
    authors: list[str] = field(default_factory=list)
    license: str = "Unknown"
    environment: str = "Unknown"


@dataclass(frozen=True)
class PackHealthSummary:
    """Computed pack health metrics derived from mod rows and scan findings."""

    clean_mod_count: int
    total_active_mods: int
    percentage: int
    pack_status: str  # "Healthy", "Warnings", "Errors"
    label: str        # e.g. "36 of 44 mods without issues"


@dataclass(frozen=True)
class FilterCriteria:
    """Criteria used to filter and sort mod rows."""

    search_text: str = ""
    loader: str | None = None
    version: str | None = None
    status: str | None = None  # "ok", "warning", "error", "disabled", or None
    has_issues: bool | None = None  # True: >0 issues, False: ==0 issues, None: all
    sort_key: str = "name"  # "name", "mod_id", "status", "loader", "version", "issue_count", "jar_name"
    sort_ascending: bool = True


def calculate_pack_health(
    rows: Sequence[ModRow],
    summary_or_result: Any,
) -> PackHealthSummary:
    """Calculate the share of active mods without error/warning findings."""
    active_mods = [r for r in rows if not r.is_disabled]
    total_active = len(active_mods)
    clean_count = sum(1 for r in active_mods if r.error_count == 0 and r.warning_count == 0)

    percentage = round((clean_count / total_active) * 100) if total_active > 0 else 100

    if hasattr(summary_or_result, "error_count"):
        err_c = summary_or_result.error_count
        warn_c = summary_or_result.warning_count
    elif hasattr(summary_or_result, "findings"):
        err_c = sum(1 for f in summary_or_result.findings if f.severity == Severity.ERROR)
        warn_c = sum(1 for f in summary_or_result.findings if f.severity == Severity.WARNING)
    else:
        err_c = sum(r.error_count for r in active_mods)
        warn_c = sum(r.warning_count for r in active_mods)

    if err_c > 0:
        pack_status = "Errors"
    elif warn_c > 0:
        pack_status = "Warnings"
    else:
        pack_status = "Healthy"

    label = f"{clean_count} of {total_active} mods without issues"

    return PackHealthSummary(
        clean_mod_count=clean_count,
        total_active_mods=total_active,
        percentage=percentage,
        pack_status=pack_status,
        label=label,
    )


def _format_loader_string(loaders: Sequence[Loader]) -> str:
    if not loaders:
        return "Unknown"
    return "/".join(l.value for l in loaders)


def _derive_row_status(
    is_disabled: bool,
    findings: Sequence[Finding],
) -> tuple[str, int, int, int]:
    """Derive status ("ok", "warning", "error", "disabled") and issue counts.

    Only non-aggregate error and warning findings affect status and issue counts.
    Info findings never raise a row above 'ok'.
    """
    err_count = sum(1 for f in findings if f.severity == Severity.ERROR)
    warn_count = sum(1 for f in findings if f.severity == Severity.WARNING)
    issue_count = err_count + warn_count

    if is_disabled:
        status = "disabled"
    elif err_count > 0:
        status = "error"
    elif warn_count > 0:
        status = "warning"
    else:
        status = "ok"

    return status, issue_count, err_count, warn_count


def build_pack_view(
    scan_result: ScanResult,
    analysis_report: AnalysisReport | None = None,
) -> list[ModRow]:
    """Build mod presentation rows from scan results and analysis findings.

    Dual-loader jars emitting multiple ModInfo instances for the same mod ID are
    consolidated into a single row representing the active record for the pack loader.
    Aggregate/pack-level findings never attach to individual mod rows.
    """
    all_findings = list(scan_result.findings)
    if analysis_report is not None:
        all_findings.extend(analysis_report.findings)

    # Filter out aggregate pack-level findings from per-mod attribution
    mod_eligible_findings = [f for f in all_findings if f.code not in PACK_LEVEL_CODES]

    jar_by_path = {j.path: j for j in scan_result.jars}

    # Group ModInfo objects by (jar_path, mod_id)
    grouped_mods: dict[tuple[str, str], list[ModInfo]] = {}
    for mod in scan_result.mods:
        key = (mod.jar_path, mod.id)
        grouped_mods.setdefault(key, []).append(mod)

    pack_loader = scan_result.detected_loader
    rows: list[ModRow] = []

    for (jar_path, mod_id), mod_list in grouped_mods.items():
        # Select active mod record based on detected loader
        active_mod = mod_list[0]
        if pack_loader is not None and len(mod_list) > 1:
            for candidate in mod_list:
                if pack_loader in candidate.loaders:
                    active_mod = candidate
                    break

        other_formats = [m for m in mod_list if m is not active_mod]

        jar_info = jar_by_path.get(jar_path)
        is_disabled = jar_info.is_disabled if jar_info is not None else False
        is_nested = active_mod.is_nested or (jar_info.is_nested if jar_info is not None else False)

        # Match findings for this mod: non-aggregate, related_mod_ids contains mod_id
        # or jar-level evidence where related_mod_ids is empty
        related_findings = [
            f for f in mod_eligible_findings
            if mod_id in f.related_mod_ids
            or (not f.related_mod_ids and any(e.file == jar_path for e in f.evidence))
        ]

        status, total_issues, err_count, warn_count = _derive_row_status(
            is_disabled=is_disabled,
            findings=related_findings,
        )

        display_name = active_mod.name.strip() if active_mod.name and active_mod.name.strip() else active_mod.id
        display_version = active_mod.version.strip() if active_mod.version and active_mod.version.strip() else "Unknown"
        loader_str = _format_loader_string(active_mod.loaders)
        jar_name = Path(jar_path).name if jar_path else "Unknown"

        description = active_mod.description.strip() if active_mod.description and active_mod.description.strip() else "Unknown"
        license_str = active_mod.license.strip() if active_mod.license and active_mod.license.strip() else "Unknown"
        env_raw = active_mod.environment.strip() if active_mod.environment and active_mod.environment.strip() else "Unknown"

        # Format environment properly
        if env_raw == "*":
            env_str = "Client & server"
        elif env_raw.lower() == "client":
            env_str = "Client only"
        elif env_raw.lower() == "server":
            env_str = "Server only"
        else:
            env_str = env_raw

        row = ModRow(
            mod_id=active_mod.id,
            name=display_name,
            version=display_version,
            loader=loader_str,
            jar_path=jar_path or "Unknown",
            jar_name=jar_name,
            is_disabled=is_disabled,
            is_nested=is_nested,
            status=status,
            issue_count=total_issues,
            error_count=err_count,
            warning_count=warn_count,
            mod_info=active_mod,
            other_formats=other_formats,
            related_findings=related_findings,
            dependencies=list(active_mod.dependencies),
            related_files=list(active_mod.related_files),
            description=description,
            authors=list(active_mod.authors),
            license=license_str,
            environment=env_str,
        )
        rows.append(row)

    return sorted(rows, key=lambda r: (r.name.lower(), r.mod_id.lower()))


def filter_and_sort(
    rows: Sequence[ModRow],
    criteria: FilterCriteria,
) -> list[ModRow]:
    """Pure function filtering and sorting mod rows deterministically."""
    filtered: list[ModRow] = []

    search_query = criteria.search_text.strip().lower()
    loader_filter = criteria.loader.strip().lower() if criteria.loader else None
    version_filter = criteria.version.strip().lower() if criteria.version else None
    status_filter = criteria.status.strip().lower() if criteria.status else None

    for row in rows:
        # Search text match over name, mod_id, jar_name, jar_path
        if search_query:
            match_found = (
                search_query in row.name.lower()
                or search_query in row.mod_id.lower()
                or search_query in row.jar_name.lower()
                or search_query in row.jar_path.lower()
            )
            if not match_found:
                continue

        # Status match
        if status_filter is not None and status_filter != "all":
            if row.status.lower() != status_filter:
                continue

        # Loader match
        if loader_filter is not None and loader_filter != "all":
            if loader_filter not in row.loader.lower():
                continue

        # Version match
        if version_filter is not None and version_filter != "all":
            if version_filter not in row.version.lower():
                continue

        # Has issues match
        if criteria.has_issues is not None:
            if criteria.has_issues and row.issue_count == 0:
                continue
            if not criteria.has_issues and row.issue_count > 0:
                continue

        filtered.append(row)

    # Sort key resolution
    key_name = criteria.sort_key.lower()

    def get_sort_value(row: ModRow) -> tuple:
        if key_name == "name":
            return (row.name.lower(), row.mod_id.lower())
        elif key_name == "mod_id" or key_name == "id":
            return (row.mod_id.lower(), row.name.lower())
        elif key_name == "status":
            status_order = {"error": 0, "warning": 1, "disabled": 2, "ok": 3}
            return (status_order.get(row.status, 9), row.name.lower())
        elif key_name == "loader":
            return (row.loader.lower(), row.name.lower())
        elif key_name == "version":
            return (row.version.lower(), row.name.lower())
        elif key_name == "issue_count" or key_name == "issues":
            return (row.issue_count, row.name.lower())
        elif key_name == "jar_name" or key_name == "jar":
            return (row.jar_name.lower(), row.name.lower())
        return (row.name.lower(), row.mod_id.lower())

    return sorted(filtered, key=get_sort_value, reverse=not criteria.sort_ascending)
