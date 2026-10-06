"""Diagnostic report service.

Aggregates profile scan snapshots, analyzers, findings, mod inventory,
conflicts by certainty, and crash reports into a structured DiagnosticReport.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
from typing import Any

from mmis.domain.crash import CrashAnalysisResult, CrashRecord
from mmis.domain.findings import (
    CATEGORY_CONFLICT,
    CATEGORY_CRASH,
    CATEGORY_DEPENDENCY,
    CATEGORY_METADATA,
    get_category,
)
from mmis.domain.models import Certainty, Evidence, Finding, ModInfo, Severity
from mmis.domain.profiles import ScanRecord


def format_certainty(c: Certainty | str) -> str:
    """Format Certainty enum or string to consistent presentation labels."""
    val = c.value if isinstance(c, Certainty) else str(c)
    lower = val.lower()
    if lower == "certain":
        return "Certain"
    if lower == "possible":
        return "Possible"
    return "Insufficient data"


def _sanitize_file_path(file_path: str | None, base_dir: str | None) -> str:
    """Ensure evidence or jar file paths do not expose absolute paths outside profile_path."""
    if not file_path:
        return ""
    p_str = str(file_path).replace("\\", "/")
    if base_dir:
        b_str = str(base_dir).replace("\\", "/").rstrip("/")
        if p_str.startswith(b_str + "/"):
            return p_str[len(b_str) + 1 :]
    # If it is an absolute path (starts with / or has drive letter like C:)
    if os.path.isabs(file_path) or (len(file_path) > 2 and file_path[1] == ":"):
        return os.path.basename(file_path)
    return p_str


@dataclass(frozen=True)
class ReportEvidence:
    """Clean evidence model for reports."""

    file: str
    field: str | None = None
    raw_value: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "field": self.field,
            "message": self.message,
            "raw_value": self.raw_value,
        }


@dataclass(frozen=True)
class ReportFinding:
    """Diagnostic finding prepared for report presentation."""

    code: str
    message: str
    severity: str
    certainty: str
    category: str
    suggestion: str | None = None
    related_mod_ids: list[str] = field(default_factory=list)
    evidence: list[ReportEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "certainty": self.certainty,
            "code": self.code,
            "evidence": [e.to_dict() for e in self.evidence],
            "message": self.message,
            "related_mod_ids": sorted(self.related_mod_ids),
            "severity": self.severity,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class Recommendation:
    """Deduplicated recommendation derived from finding suggestions."""

    suggestion: str
    severity: str
    finding_codes: list[str]
    affected_mod_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "affected_mod_ids": sorted(self.affected_mod_ids),
            "finding_codes": sorted(self.finding_codes),
            "severity": self.severity,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class ModInventoryItem:
    """Single mod in the inventory summary."""

    mod_id: str
    name: str
    version: str
    loader: str
    jar_name: str
    is_disabled: bool
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "is_disabled": self.is_disabled,
            "jar_name": self.jar_name,
            "loader": self.loader,
            "mod_id": self.mod_id,
            "name": self.name,
            "version": self.version,
        }


@dataclass(frozen=True)
class ModInventorySummary:
    """Aggregated modpack inventory details."""

    total_mods: int
    total_jars: int
    enabled_mods: int
    disabled_mods: int
    mods: list[ModInventoryItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "disabled_mods": self.disabled_mods,
            "enabled_mods": self.enabled_mods,
            "mods": [m.to_dict() for m in self.mods],
            "total_jars": self.total_jars,
            "total_mods": self.total_mods,
        }


@dataclass(frozen=True)
class CrashReportSummary:
    """Summarized crash analysis included in diagnostic report."""

    crash_id: int
    imported_at: str
    source_name: str
    kind: str
    mc_version: str | None
    loader: str | None
    root_cause: str | None
    top_suspect: str | None
    error_count: int
    warning_count: int
    suspects: list[dict[str, Any]]
    findings: list[ReportFinding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "crash_id": self.crash_id,
            "error_count": self.error_count,
            "findings": [f.to_dict() for f in self.findings],
            "imported_at": self.imported_at,
            "kind": self.kind,
            "loader": self.loader,
            "mc_version": self.mc_version,
            "root_cause": self.root_cause,
            "source_name": self.source_name,
            "suspects": self.suspects,
            "top_suspect": self.top_suspect,
            "warning_count": self.warning_count,
        }


@dataclass(frozen=True)
class DiagnosticReport:
    """Comprehensive diagnostic report for a modpack scan snapshot."""

    schema_version: int
    profile_name: str
    profile_path: str
    scan_id: int
    scanned_at: str
    scan_label: str | None
    detected_loader: str | None
    detected_mc_version: str | None
    status: str
    severity_counts: dict[str, int]
    certainty_counts: dict[str, int]
    category_counts: dict[str, int]
    recommendations: list[Recommendation]
    findings_by_category: dict[str, list[ReportFinding]]
    dependency_problems: list[ReportFinding]
    conflicts_by_certainty: dict[str, list[ReportFinding]]
    mod_inventory: ModInventorySummary
    latest_crash: CrashReportSummary | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_counts": {k: self.category_counts[k] for k in sorted(self.category_counts)},
            "certainty_counts": {k: self.certainty_counts[k] for k in sorted(self.certainty_counts)},
            "conflicts_by_certainty": {
                k: [f.to_dict() for f in self.conflicts_by_certainty.get(k, [])]
                for k in ["Certain", "Possible", "Insufficient data"]
            },
            "dependency_problems": [f.to_dict() for f in self.dependency_problems],
            "detected_loader": self.detected_loader,
            "detected_mc_version": self.detected_mc_version,
            "findings_by_category": {
                k: [f.to_dict() for f in self.findings_by_category[k]]
                for k in sorted(self.findings_by_category)
            },
            "latest_crash": self.latest_crash.to_dict() if self.latest_crash else None,
            "mod_inventory": self.mod_inventory.to_dict(),
            "profile_name": self.profile_name,
            "profile_path": self.profile_path,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "scan_id": self.scan_id,
            "scan_label": self.scan_label,
            "scanned_at": self.scanned_at,
            "schema_version": self.schema_version,
            "severity_counts": {k: self.severity_counts[k] for k in sorted(self.severity_counts)},
            "status": self.status,
        }


def _severity_rank(sev: str | Severity) -> int:
    val = sev.value if isinstance(sev, Severity) else str(sev).lower()
    if val == "error":
        return 0
    if val == "warning":
        return 1
    return 2


def _convert_finding(f: Finding, base_dir: str | None) -> ReportFinding:
    cat = get_category(f.code)
    ev_list = [
        ReportEvidence(
            file=_sanitize_file_path(e.file, base_dir),
            field=e.field,
            raw_value=e.raw_value,
            message=e.message,
        )
        for e in f.evidence
    ]
    return ReportFinding(
        code=f.code,
        message=f.message,
        severity=f.severity.value if isinstance(f.severity, Severity) else str(f.severity),
        certainty=format_certainty(f.certainty),
        category=cat,
        suggestion=f.suggestion,
        related_mod_ids=sorted(f.related_mod_ids),
        evidence=ev_list,
    )


def _sort_report_finding(f: ReportFinding) -> tuple:
    return (_severity_rank(f.severity), f.code, f.message, f.related_mod_ids)


def build_diagnostic_report(
    profile_name: str,
    scan_record: ScanRecord,
    profile_path: str | None = None,
    latest_crash: tuple[CrashRecord, CrashAnalysisResult] | None = None,
) -> DiagnosticReport:
    """Build a deterministic DiagnosticReport for a profile scan."""
    scan_res = scan_record.result
    analysis_rep = scan_record.analysis
    summary = scan_record.summary

    base_dir = profile_path or scan_res.scanned_path or ""

    # Combine findings from scan (metadata) and analysis (dependencies, conflicts)
    combined_findings: list[Finding] = list(scan_res.findings) + list(analysis_rep.findings)
    report_findings = [_convert_finding(f, base_dir) for f in combined_findings]
    report_findings.sort(key=_sort_report_finding)

    # Status: Healthy, Warnings, Errors
    err_c = summary.error_count
    warn_c = summary.warning_count
    info_c = summary.info_count
    if err_c > 0:
        status = "Errors"
    elif warn_c > 0:
        status = "Warnings"
    else:
        status = "Healthy"

    severity_counts = {
        "error": err_c,
        "warning": warn_c,
        "info": info_c,
    }

    # Certainty counts with exact required labels
    certainty_counts = {
        "Certain": sum(1 for f in report_findings if f.certainty == "Certain"),
        "Possible": sum(1 for f in report_findings if f.certainty == "Possible"),
        "Insufficient data": sum(1 for f in report_findings if f.certainty == "Insufficient data"),
    }

    # Category counts & grouping
    category_counts: dict[str, int] = {
        CATEGORY_METADATA: 0,
        CATEGORY_DEPENDENCY: 0,
        CATEGORY_CONFLICT: 0,
    }
    findings_by_category: dict[str, list[ReportFinding]] = {
        CATEGORY_METADATA: [],
        CATEGORY_DEPENDENCY: [],
        CATEGORY_CONFLICT: [],
    }

    for f in report_findings:
        cat = f.category
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if cat not in findings_by_category:
            findings_by_category[cat] = []
        findings_by_category[cat].append(f)

    # Dependency problems
    dependency_problems = [f for f in report_findings if f.category == CATEGORY_DEPENDENCY]
    dependency_problems.sort(key=_sort_report_finding)

    # Conflicts by certainty class
    conflicts = [f for f in report_findings if f.category == CATEGORY_CONFLICT]
    conflicts_by_certainty: dict[str, list[ReportFinding]] = {
        "Certain": sorted([f for f in conflicts if f.certainty == "Certain"], key=_sort_report_finding),
        "Possible": sorted([f for f in conflicts if f.certainty == "Possible"], key=_sort_report_finding),
        "Insufficient data": sorted([f for f in conflicts if f.certainty == "Insufficient data"], key=_sort_report_finding),
    }

    # Recommendations: deduplicated suggestions ordered by error > warning > info, then affected mods count, then text
    sug_map: dict[str, dict[str, Any]] = {}
    for f in report_findings:
        if f.suggestion and f.suggestion.strip():
            clean_sug = f.suggestion.strip()
            if clean_sug not in sug_map:
                sug_map[clean_sug] = {
                    "severities": set(),
                    "codes": set(),
                    "mods": set(),
                }
            sug_map[clean_sug]["severities"].add(f.severity)
            sug_map[clean_sug]["codes"].add(f.code)
            sug_map[clean_sug]["mods"].update(f.related_mod_ids)

    recommendations: list[Recommendation] = []
    for sug_text, data in sug_map.items():
        # Highest severity
        min_rank = min(_severity_rank(s) for s in data["severities"])
        sev_str = "error" if min_rank == 0 else ("warning" if min_rank == 1 else "info")
        recommendations.append(
            Recommendation(
                suggestion=sug_text,
                severity=sev_str,
                finding_codes=sorted(data["codes"]),
                affected_mod_ids=sorted(data["mods"]),
            )
        )

    recommendations.sort(
        key=lambda r: (_severity_rank(r.severity), -len(r.affected_mod_ids), r.suggestion)
    )

    # Mod inventory
    mod_items: list[ModInventoryItem] = []
    # Build jar map for disabled status
    disabled_jar_names = {os.path.basename(j.path) for j in scan_res.jars if j.is_disabled}

    for m in scan_res.mods:
        j_name = os.path.basename(m.jar_path)
        is_dis = bool(getattr(m, "is_disabled", False) or (j_name in disabled_jar_names))
        m_loader = (
            m.loaders[0].value if (getattr(m, "loaders", None) and hasattr(m.loaders[0], "value"))
            else (str(m.loaders[0]) if getattr(m, "loaders", None) else "Unknown")
        )
        mod_items.append(
            ModInventoryItem(
                mod_id=m.id,
                name=m.name or m.id,
                version=m.version or "Unknown",
                loader=m_loader,
                jar_name=j_name,
                is_disabled=is_dis,
                description=m.description,
            )
        )
    mod_items.sort(key=lambda item: (item.name.lower(), item.mod_id))

    mod_inventory = ModInventorySummary(
        total_mods=len(mod_items),
        total_jars=len(scan_res.jars),
        enabled_mods=sum(1 for m in mod_items if not m.is_disabled),
        disabled_mods=sum(1 for m in mod_items if m.is_disabled),
        mods=mod_items,
    )

    # Crash analysis summary (if provided)
    crash_summary: CrashReportSummary | None = None
    if latest_crash:
        c_rec, c_analysis = latest_crash
        crash_findings = [_convert_finding(f, base_dir) for f in c_analysis.findings]
        crash_findings.sort(key=_sort_report_finding)

        root_cause_str = None
        if c_analysis.root_cause:
            msg = f": {c_analysis.root_cause.message}" if c_analysis.root_cause.message else ""
            root_cause_str = f"{c_analysis.root_cause.exception_class}{msg}"

        suspects_data = [
            {
                "attribution": s.attribution,
                "certainty": format_certainty(s.certainty),
                "confidence_score": 100 if s.rank == 1 else (75 if s.rank == 2 else 50),
                "evidence_lines": [f"L{e[0]}: {e[1]}" if isinstance(e, (list, tuple)) else str(e) for e in s.evidence_lines],
                "mod_id": s.mod_id,
                "mod_name": s.name or s.mod_id,
                "rank": s.rank,
                "reason": s.reason,
            }
            for s in sorted(c_analysis.suspects, key=lambda s: s.rank)
        ]
        crash_summary = CrashReportSummary(
            crash_id=c_rec.id,
            imported_at=c_rec.imported_at,
            source_name=c_rec.source_name,
            kind=c_analysis.kind.value,
            mc_version=c_analysis.mc_version,
            loader=c_analysis.detected_loader,
            root_cause=root_cause_str,
            top_suspect=c_rec.top_suspect,
            error_count=c_rec.error_count,
            warning_count=c_rec.warning_count,
            suspects=suspects_data,
            findings=crash_findings,
        )

    return DiagnosticReport(
        schema_version=1,
        profile_name=profile_name,
        profile_path=str(base_dir),
        scan_id=summary.id,
        scanned_at=summary.scanned_at,
        scan_label=summary.label,
        detected_loader=summary.loader,
        detected_mc_version=summary.mc_version,
        status=status,
        severity_counts=severity_counts,
        certainty_counts=certainty_counts,
        category_counts=category_counts,
        recommendations=recommendations,
        findings_by_category=findings_by_category,
        dependency_problems=dependency_problems,
        conflicts_by_certainty=conflicts_by_certainty,
        mod_inventory=mod_inventory,
        latest_crash=crash_summary,
    )
