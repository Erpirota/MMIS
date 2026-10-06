"""Pure functional view model and filtering logic for the Issues page.

Contains zero Qt dependencies. Prepares structured diagnostic findings for UI
filtering, grouping, search, and detail expansion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from mmis.domain.findings import (
    AGGREGATE_CODES,
    CATEGORY_CONFLICT,
    CATEGORY_DEPENDENCY,
    CATEGORY_METADATA,
    get_category,
)
from mmis.domain.models import Certainty, Evidence, Finding, Severity
from mmis.domain.profiles import ScanRecord
from mmis.services.report import format_certainty

PACK_LEVEL_CODES = frozenset(
    AGGREGATE_CODES | {
        "AMBIGUOUS_MC_VERSION",
        "CONFLICTING_MANIFEST",
        "PACK_MANIFEST_DETECTED",
        "INVALID_PACK_MANIFEST",
    }
)


@dataclass(frozen=True)
class IssueEvidence:
    """Evidence line for an issue."""

    file: str
    field: str | None = None
    raw_value: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class IssueItem:
    """Presentation item for a single diagnostic finding."""

    id: str
    code: str
    category: str
    severity: str  # "error" | "warning" | "info"
    certainty: str  # "Certain" | "Possible" | "Insufficient data"
    message: str
    suggestion: str | None
    evidence: list[IssueEvidence]
    related_mods: list[str]
    is_pack_level: bool


@dataclass(frozen=True)
class IssueFilterCriteria:
    """Filter and grouping criteria for issues."""

    severities: set[str] = field(default_factory=lambda: {"error", "warning", "info"})
    certainties: set[str] = field(default_factory=lambda: {"Certain", "Possible", "Insufficient data"})
    categories: set[str] = field(default_factory=lambda: {"metadata", "dependency", "conflict", "crash"})
    search_text: str = ""
    pack_level_only: bool = False
    group_by: str = "none"  # "none" | "category" | "severity" | "mod"


def _severity_rank(sev: str) -> int:
    s = sev.lower()
    if s == "error":
        return 0
    if s == "warning":
        return 1
    return 2


def build_issues_view(scan_record: ScanRecord) -> list[IssueItem]:
    """Convert scan and analysis findings from ScanRecord into structured IssueItems."""
    scan_res = scan_record.result
    analysis_rep = scan_record.analysis

    combined = list(scan_res.findings)
    if analysis_rep is not None and hasattr(analysis_rep, "findings") and analysis_rep.findings:
        combined.extend(analysis_rep.findings)
    items: list[IssueItem] = []

    for idx, f in enumerate(combined):
        cat = get_category(f.code)
        sev_str = f.severity.value if hasattr(f.severity, "value") else str(f.severity).lower()
        cert_label = format_certainty(f.certainty)

        ev_list = [
            IssueEvidence(
                file=e.file,
                field=e.field,
                raw_value=e.raw_value,
                message=e.message,
            )
            for e in f.evidence
        ]

        is_pack = bool(not f.related_mod_ids or f.code in PACK_LEVEL_CODES)
        rel_mods = sorted(f.related_mod_ids)
        item_id = f"{f.code}_{idx}_{rel_mods[0] if rel_mods else 'pack'}"

        items.append(
            IssueItem(
                id=item_id,
                code=f.code,
                category=cat,
                severity=sev_str,
                certainty=cert_label,
                message=f.message,
                suggestion=f.suggestion,
                evidence=ev_list,
                related_mods=rel_mods,
                is_pack_level=is_pack,
            )
        )

    # Deterministic default order: error > warning > info, then code, then first related mod
    items.sort(
        key=lambda item: (
            _severity_rank(item.severity),
            item.code,
            item.related_mods[0] if item.related_mods else "",
            item.message,
        )
    )
    return items


def filter_issues(items: Sequence[IssueItem], criteria: IssueFilterCriteria) -> list[IssueItem]:
    """Filter issue items by severities, certainties, categories, search text, and pack-level."""
    query = criteria.search_text.strip().lower()
    filtered: list[IssueItem] = []

    for it in items:
        if it.severity not in criteria.severities:
            continue
        if it.certainty not in criteria.certainties:
            continue
        if it.category not in criteria.categories:
            continue
        if criteria.pack_level_only and not it.is_pack_level:
            continue

        if query:
            # Match text over code, message, or related mod IDs
            matches_code = query in it.code.lower()
            matches_msg = query in it.message.lower()
            matches_mods = any(query in m.lower() for m in it.related_mods)
            if not (matches_code or matches_msg or matches_mods):
                continue

        filtered.append(it)

    # Ensure deterministic ordering: error > warning > info, then code, then first mod
    filtered.sort(
        key=lambda item: (
            _severity_rank(item.severity),
            item.code,
            item.related_mods[0] if item.related_mods else "",
            item.message,
        )
    )
    return filtered


def group_issues(items: Sequence[IssueItem], group_by: str) -> dict[str, list[IssueItem]]:
    """Group issue items by category, severity, or related mod."""
    if group_by == "category":
        order = [CATEGORY_METADATA, CATEGORY_DEPENDENCY, CATEGORY_CONFLICT, "crash"]
        groups: dict[str, list[IssueItem]] = {k: [] for k in order}
        for it in items:
            groups.setdefault(it.category, []).append(it)
        return {k: v for k, v in groups.items() if v}

    elif group_by == "severity":
        order = ["error", "warning", "info"]
        groups = {k: [] for k in order}
        for it in items:
            groups.setdefault(it.severity, []).append(it)
        return {k: v for k, v in groups.items() if v}

    elif group_by == "mod":
        groups = {}
        for it in items:
            if not it.related_mods:
                groups.setdefault("Pack-level", []).append(it)
            else:
                for mod_id in it.related_mods:
                    groups.setdefault(mod_id, []).append(it)
        # Sort groups alphabetically by mod id, with Pack-level first if present
        sorted_keys = sorted(groups.keys(), key=lambda k: (0 if k == "Pack-level" else 1, k.lower()))
        return {k: groups[k] for k in sorted_keys}

    return {"All Issues": list(items)}
