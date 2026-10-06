"""Presentation view models and pure data access helpers for crash reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mmis.services.report import format_certainty

if TYPE_CHECKING:
    from mmis.services.profiles import ProfileService


UNLINKED_LABEL = "Unlinked"

KIND_LABELS: dict[str, str] = {
    "crash_report": "Crash Report",
    "log_file": "Log File",
    "hs_err": "JVM Error (hs_err)",
    "unknown": "Unknown",
}


def format_imported_at(iso_str: str) -> str:
    """Format an ISO timestamp to human-readable 'YYYY-MM-DD HH:MM UTC'."""
    if not iso_str:
        return ""
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        if "T" in iso_str:
            date_part, rest = iso_str.split("T", 1)
            time_part = rest[:5]
            return f"{date_part} {time_part} UTC"
        return iso_str


def format_crash_kind(kind: str) -> str:
    """Map CrashReportKind enum value to human-readable label, falling back to raw value."""
    if not kind:
        return ""
    return KIND_LABELS.get(kind.lower(), kind)


@dataclass(frozen=True)
class CrashListItem:
    """Summary item for a crash report shown in lists."""

    id: int
    source_name: str
    imported_at: str
    kind: str
    mc_version: str | None
    loader: str | None
    top_suspect: str | None
    error_count: int
    warning_count: int
    info_count: int
    file_size: int
    sha256: str
    profile_name: str | None  # None when crash is not linked to a profile

    @property
    def display_imported_at(self) -> str:
        return format_imported_at(self.imported_at)

    @property
    def display_kind(self) -> str:
        return format_crash_kind(self.kind)

    @property
    def display_profile_name(self) -> str:
        return self.profile_name if self.profile_name else UNLINKED_LABEL


@dataclass(frozen=True)
class CrashSuspectView:
    """View model for a suspected mod in a crash report."""

    mod_id: str
    name: str
    certainty: str  # "Certain" | "Possible" | "Insufficient data"
    attribution: str
    reason: str
    evidence_lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CrashFindingView:
    """View model for a diagnostic finding in a crash report."""

    code: str
    severity: str
    certainty: str  # "Certain" | "Possible" | "Insufficient data"
    message: str
    suggestion: str | None = None


@dataclass(frozen=True)
class CrashDetail:
    """Full detail view model for an analyzed crash report."""

    item: CrashListItem
    description: str | None
    java_version: str | None
    root_cause_class: str | None
    root_cause_message: str | None
    is_truncated: bool
    suspects: list[CrashSuspectView] = field(default_factory=list)
    findings: list[CrashFindingView] = field(default_factory=list)


def sha256_file(path: str | Path) -> str:
    """Compute SHA-256 hash of a file in constant memory using 1 MB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_crashes(service: ProfileService, profile_name: str | None) -> list[CrashListItem]:
    """List crash reports ordered newest first.

    If profile_name is None, returns all crashes across all profiles plus unlinked ones.
    If profile_name is specified, returns only crashes linked to that profile.
    """
    records = service.list_crashes(profile_name=profile_name)
    profiles_map = {p.id: p.name for p in service.list_profiles()}

    items: list[CrashListItem] = []
    for r in records:
        if profile_name is not None:
            p_name = profile_name
        else:
            p_name = profiles_map.get(r.profile_id) if r.profile_id is not None else None

        items.append(
            CrashListItem(
                id=r.id,
                source_name=r.source_name,
                imported_at=r.imported_at,
                kind=r.kind,
                mc_version=r.mc_version,
                loader=r.loader,
                top_suspect=r.top_suspect,
                error_count=r.error_count,
                warning_count=r.warning_count,
                info_count=r.info_count,
                file_size=r.file_size,
                sha256=r.sha256,
                profile_name=p_name,
            )
        )
    return items


def get_crash_detail(service: ProfileService, crash_id: int) -> CrashDetail:
    """Retrieve structured CrashDetail for a stored crash report."""
    rec, ana = service.get_crash(crash_id)
    profile_name: str | None = None
    if rec.profile_id is not None:
        p = service.repo.get_profile_by_id(rec.profile_id)
        if p is not None:
            profile_name = p.name

    item = CrashListItem(
        id=rec.id,
        source_name=rec.source_name,
        imported_at=rec.imported_at,
        kind=rec.kind,
        mc_version=rec.mc_version,
        loader=rec.loader,
        top_suspect=rec.top_suspect,
        error_count=rec.error_count,
        warning_count=rec.warning_count,
        info_count=rec.info_count,
        file_size=rec.file_size,
        sha256=rec.sha256,
        profile_name=profile_name,
    )

    suspects = [
        CrashSuspectView(
            mod_id=s.mod_id,
            name=s.name or s.mod_id,
            certainty=format_certainty(s.certainty),
            attribution=s.attribution,
            reason=s.reason,
            evidence_lines=list(s.evidence_lines),
        )
        for s in ana.suspects
    ]

    findings = [
        CrashFindingView(
            code=f.code,
            severity=f.severity.value if hasattr(f.severity, "value") else str(f.severity),
            certainty=format_certainty(f.certainty),
            message=f.message,
            suggestion=f.suggestion,
        )
        for f in ana.findings
    ]

    rc_class = ana.root_cause.exception_class if ana.root_cause else None
    rc_msg = ana.root_cause.message if ana.root_cause else None

    return CrashDetail(
        item=item,
        description=ana.description,
        java_version=ana.java_version,
        root_cause_class=rc_class,
        root_cause_message=rc_msg,
        is_truncated=ana.is_truncated,
        suspects=suspects,
        findings=findings,
    )


def find_duplicate(service: ProfileService, sha256: str, profile_name: str | None) -> int | None:
    """Find existing crash report matching sha256 and profile link."""
    if profile_name is not None:
        try:
            profile = service.get_profile(profile_name)
            target_profile_id = profile.id
        except Exception:
            return None
        crashes = service.list_crashes(profile_name=profile_name)
        for c in crashes:
            if c.sha256 == sha256 and c.profile_id == target_profile_id:
                return c.id
    else:
        # unlinked crash: profile_id is None
        crashes = service.list_crashes(profile_name=None)
        for c in crashes:
            if c.sha256 == sha256 and c.profile_id is None:
                return c.id
    return None
