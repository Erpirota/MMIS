"""Analysis engine for modpack scans.

Coordinates registered analyzers and aggregates diagnostic findings into an AnalysisReport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from mmis.analysis.conflicts import analyze_conflicts
from mmis.analysis.dependencies import analyze_dependencies
from mmis.domain.findings import CODE_TO_CATEGORY, get_category
from mmis.domain.models import Certainty, Finding, ScanResult, Severity

AnalyzerFunc = Callable[[ScanResult], list[Finding]]

# Registered analyzers evaluated in order during analyze_pack().
# This list is the extension point for MMIS analyzers:
# functions must have signature (ScanResult) -> list[Finding].
# Findings returned by all analyzers are aggregated, sorted deterministically,
# and classified by severity, certainty, and category.
ANALYZERS: list[AnalyzerFunc] = [
    analyze_dependencies,
    analyze_conflicts,
]


def _sort_finding_key(f: Finding) -> tuple:
    return (
        f.code,
        f.message,
        tuple((e.file, e.field or "", e.raw_value or "", e.message or "") for e in f.evidence),
    )


@dataclass(frozen=True)
class AnalysisReport:
    """Aggregated analysis report produced by running analyzers over a ScanResult."""

    findings: list[Finding] = field(default_factory=list)
    severity_counts: dict[str, int] = field(default_factory=dict)
    certainty_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        sorted_findings = sorted(self.findings, key=_sort_finding_key)
        return {
            "category_counts": {k: self.category_counts[k] for k in sorted(self.category_counts.keys())},
            "certainty_counts": {k: self.certainty_counts[k] for k in sorted(self.certainty_counts.keys())},
            "findings": [f.to_dict() for f in sorted_findings],
            "severity_counts": {k: self.severity_counts[k] for k in sorted(self.severity_counts.keys())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisReport:
        return cls(
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            severity_counts=dict(data.get("severity_counts", {})),
            certainty_counts=dict(data.get("certainty_counts", {})),
            category_counts=dict(data.get("category_counts", {})),
        )


def analyze_pack(scan: ScanResult) -> AnalysisReport:
    """Run all registered analyzers on a ScanResult and return an AnalysisReport."""
    findings: list[Finding] = []
    for analyzer in ANALYZERS:
        findings.extend(analyzer(scan))

    findings = sorted(findings, key=_sort_finding_key)

    severity_counts = {
        Severity.ERROR.value: sum(1 for f in findings if f.severity == Severity.ERROR),
        Severity.WARNING.value: sum(1 for f in findings if f.severity == Severity.WARNING),
        Severity.INFO.value: sum(1 for f in findings if f.severity == Severity.INFO),
    }

    certainty_counts = {
        Certainty.CERTAIN.value: sum(1 for f in findings if f.certainty == Certainty.CERTAIN),
        Certainty.POSSIBLE.value: sum(1 for f in findings if f.certainty == Certainty.POSSIBLE),
        Certainty.INSUFFICIENT_DATA.value: sum(1 for f in findings if f.certainty == Certainty.INSUFFICIENT_DATA),
    }

    category_counts: dict[str, int] = {}
    for f in findings:
        cat = get_category(f.code)
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return AnalysisReport(
        findings=findings,
        severity_counts=severity_counts,
        certainty_counts=certainty_counts,
        category_counts=category_counts,
    )
