"""Pure functional diff engine for comparing ScanResult snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mmis.analysis.engine import analyze_pack
from mmis.domain.findings import AGGREGATE_CODES
from mmis.domain.models import Finding, ModInfo, ScanResult


@dataclass(frozen=True)
class ModDiff:
    """Differences observed for a single mod ID between two scans."""

    id: str
    change_type: str  # "added" | "removed" | "changed"
    old_version: str | None = None
    new_version: str | None = None
    old_sha256: str | None = None
    new_sha256: str | None = None
    old_jar_path: str | None = None
    new_jar_path: str | None = None
    old_disabled: bool | None = None
    new_disabled: bool | None = None
    is_nested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type,
            "id": self.id,
            "is_nested": self.is_nested,
            "new_disabled": self.new_disabled,
            "new_jar_path": self.new_jar_path,
            "new_sha256": self.new_sha256,
            "new_version": self.new_version,
            "old_disabled": self.old_disabled,
            "old_jar_path": self.old_jar_path,
            "old_sha256": self.old_sha256,
            "old_version": self.old_version,
        }


@dataclass(frozen=True)
class FindingDiff:
    """Differences observed for diagnostic findings between two scans."""

    code: str
    message: str
    severity: str
    related_mod_ids: list[str]
    evidence_files: list[str]
    change_type: str  # "added" | "resolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type,
            "code": self.code,
            "evidence_files": sorted(self.evidence_files),
            "message": self.message,
            "related_mod_ids": sorted(self.related_mod_ids),
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ScanDiff:
    """Aggregated structural diff between two modpack scans."""

    loader_changed: dict[str, str | None] | None = None
    mc_version_changed: dict[str, str | None] | None = None
    top_level_mods: list[ModDiff] = field(default_factory=list)
    nested_mods: list[ModDiff] = field(default_factory=list)
    findings_added: list[FindingDiff] = field(default_factory=list)
    findings_resolved: list[FindingDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Return True if any difference exists between the two scans."""
        return bool(
            self.loader_changed is not None
            or self.mc_version_changed is not None
            or self.top_level_mods
            or self.nested_mods
            or self.findings_added
            or self.findings_resolved
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings_added": [f.to_dict() for f in sorted(self.findings_added, key=lambda x: (x.code, x.message))],
            "findings_resolved": [f.to_dict() for f in sorted(self.findings_resolved, key=lambda x: (x.code, x.message))],
            "has_changes": self.has_changes,
            "loader_changed": self.loader_changed,
            "mc_version_changed": self.mc_version_changed,
            "nested_mods": [m.to_dict() for m in sorted(self.nested_mods, key=lambda x: x.id)],
            "top_level_mods": [m.to_dict() for m in sorted(self.top_level_mods, key=lambda x: x.id)],
        }


def _finding_identity(f: Finding) -> tuple:
    if f.code in AGGREGATE_CODES:
        return (f.code,)
    evidence_files = sorted(set(e.file for e in f.evidence if e.file))
    return (f.code, tuple(sorted(f.related_mod_ids)), tuple(evidence_files))


def diff_scans(a: ScanResult, b: ScanResult) -> ScanDiff:
    """Compute the structural difference between scan A (old) and scan B (new)."""
    # 1. Loader & MC Version changes
    old_loader = a.detected_loader.value if a.detected_loader else None
    new_loader = b.detected_loader.value if b.detected_loader else None
    loader_diff = {"new": new_loader, "old": old_loader} if old_loader != new_loader else None

    old_mc = a.detected_mc_version
    new_mc = b.detected_mc_version
    mc_diff = {"new": new_mc, "old": old_mc} if old_mc != new_mc else None

    # 2. Jar lookups
    jars_a = {j.path: j for j in a.jars}
    jars_b = {j.path: j for j in b.jars}

    # 3. Partition mods into top-level and nested
    def _partition_mods(mods: list[ModInfo]) -> tuple[dict[str, ModInfo], dict[str, ModInfo]]:
        top: dict[str, ModInfo] = {}
        nested: dict[str, ModInfo] = {}
        for m in mods:
            if m.is_nested:
                nested[m.id] = m
            else:
                top[m.id] = m
        return top, nested

    top_a, nested_a = _partition_mods(a.mods)
    top_b, nested_b = _partition_mods(b.mods)

    def _diff_mod_groups(
        mods_old: dict[str, ModInfo],
        mods_new: dict[str, ModInfo],
        is_nested: bool,
    ) -> list[ModDiff]:
        diffs: list[ModDiff] = []
        all_ids = sorted(set(mods_old.keys()) | set(mods_new.keys()))

        for mod_id in all_ids:
            mod_o = mods_old.get(mod_id)
            mod_n = mods_new.get(mod_id)

            if mod_o is None and mod_n is not None:
                jar_n = jars_b.get(mod_n.jar_path)
                diffs.append(
                    ModDiff(
                        id=mod_id,
                        change_type="added",
                        new_version=mod_n.version,
                        new_sha256=jar_n.sha256 if jar_n else None,
                        new_jar_path=mod_n.jar_path,
                        new_disabled=jar_n.is_disabled if jar_n else None,
                        is_nested=is_nested,
                    )
                )
            elif mod_o is not None and mod_n is None:
                jar_o = jars_a.get(mod_o.jar_path)
                diffs.append(
                    ModDiff(
                        id=mod_id,
                        change_type="removed",
                        old_version=mod_o.version,
                        old_sha256=jar_o.sha256 if jar_o else None,
                        old_jar_path=mod_o.jar_path,
                        old_disabled=jar_o.is_disabled if jar_o else None,
                        is_nested=is_nested,
                    )
                )
            elif mod_o is not None and mod_n is not None:
                jar_o = jars_a.get(mod_o.jar_path)
                jar_n = jars_b.get(mod_n.jar_path)

                old_v = mod_o.version
                new_v = mod_n.version
                old_sha = jar_o.sha256 if jar_o else None
                new_sha = jar_n.sha256 if jar_n else None
                old_path = mod_o.jar_path
                new_path = mod_n.jar_path
                old_dis = jar_o.is_disabled if jar_o else None
                new_dis = jar_n.is_disabled if jar_n else None

                # Detect if any attribute changed
                if (
                    old_v != new_v
                    or old_sha != new_sha
                    or old_path != new_path
                    or old_dis != new_dis
                ):
                    diffs.append(
                        ModDiff(
                            id=mod_id,
                            change_type="changed",
                            old_version=old_v,
                            new_version=new_v,
                            old_sha256=old_sha,
                            new_sha256=new_sha,
                            old_jar_path=old_path,
                            new_jar_path=new_path,
                            old_disabled=old_dis,
                            new_disabled=new_dis,
                            is_nested=is_nested,
                        )
                    )

        return diffs

    top_diffs = _diff_mod_groups(top_a, top_b, is_nested=False)
    nested_diffs = _diff_mod_groups(nested_a, nested_b, is_nested=True)

    # 4. Findings diff (including analysis findings)
    findings_a = list(a.findings)
    findings_b = list(b.findings)

    analysis_a = analyze_pack(a)
    analysis_b = analyze_pack(b)

    seen_a = set(_finding_identity(f) for f in findings_a)
    for f in analysis_a.findings:
        ident = _finding_identity(f)
        if ident not in seen_a:
            findings_a.append(f)
            seen_a.add(ident)

    seen_b = set(_finding_identity(f) for f in findings_b)
    for f in analysis_b.findings:
        ident = _finding_identity(f)
        if ident not in seen_b:
            findings_b.append(f)
            seen_b.add(ident)

    map_findings_a: dict[tuple, Finding] = {
        _finding_identity(f): f for f in findings_a
    }
    map_findings_b: dict[tuple, Finding] = {
        _finding_identity(f): f for f in findings_b
    }

    findings_added: list[FindingDiff] = []
    for key, f in sorted(map_findings_b.items()):
        if key not in map_findings_a:
            findings_added.append(
                FindingDiff(
                    code=f.code,
                    message=f.message,
                    severity=f.severity.value,
                    related_mod_ids=list(f.related_mod_ids),
                    evidence_files=sorted(set(e.file for e in f.evidence if e.file)),
                    change_type="added",
                )
            )

    findings_resolved: list[FindingDiff] = []
    for key, f in sorted(map_findings_a.items()):
        if key not in map_findings_b:
            findings_resolved.append(
                FindingDiff(
                    code=f.code,
                    message=f.message,
                    severity=f.severity.value,
                    related_mod_ids=list(f.related_mod_ids),
                    evidence_files=sorted(set(e.file for e in f.evidence if e.file)),
                    change_type="resolved",
                )
            )

    return ScanDiff(
        loader_changed=loader_diff,
        mc_version_changed=mc_diff,
        top_level_mods=top_diffs,
        nested_mods=nested_diffs,
        findings_added=findings_added,
        findings_resolved=findings_resolved,
    )
