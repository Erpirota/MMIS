"""Domain models for crash and log analysis.

Pure stdlib dataclasses for structured representation of Minecraft crash reports,
logs, JVM errors, stack frames, exception chains, and suspects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mmis.domain.models import Certainty, Finding


class CrashReportKind(StrEnum):
    """Classification of crash / log input type."""

    CRASH_REPORT = "crash_report"
    LOG_FILE = "log_file"
    HS_ERR = "hs_err"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StackFrame:
    """A single execution frame in a Java stack trace."""

    class_name: str
    method_name: str
    file_name: str | None = None
    line_number: int | None = None
    jar_file: str | None = None
    jar_version: str | None = None
    mixin_info: str | None = None
    is_native: bool = False
    log_line_number: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_name": self.class_name,
            "file_name": self.file_name,
            "is_native": self.is_native,
            "jar_file": self.jar_file,
            "jar_version": self.jar_version,
            "line_number": self.line_number,
            "log_line_number": self.log_line_number,
            "method_name": self.method_name,
            "mixin_info": self.mixin_info,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StackFrame:
        return cls(
            class_name=data["class_name"],
            method_name=data["method_name"],
            file_name=data.get("file_name"),
            line_number=data.get("line_number"),
            jar_file=data.get("jar_file"),
            jar_version=data.get("jar_version"),
            mixin_info=data.get("mixin_info"),
            is_native=data.get("is_native", False),
            log_line_number=data.get("log_line_number", 0),
        )


@dataclass(frozen=True)
class ExceptionInfo:
    """Structured information about an exception in the exception chain."""

    exception_class: str
    message: str | None = None
    frames: list[StackFrame] = field(default_factory=list)
    caused_by: ExceptionInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "caused_by": self.caused_by.to_dict() if self.caused_by is not None else None,
            "exception_class": self.exception_class,
            "frames": [f.to_dict() for f in self.frames],
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExceptionInfo:
        cb_data = data.get("caused_by")
        caused_by = cls.from_dict(cb_data) if cb_data is not None else None
        return cls(
            exception_class=data["exception_class"],
            message=data.get("message"),
            frames=[StackFrame.from_dict(f) for f in data.get("frames", [])],
            caused_by=caused_by,
        )


@dataclass(frozen=True)
class CrashSuspect:
    """A mod identified as a potential or certain cause of a crash."""

    mod_id: str | None
    name: str | None
    certainty: Certainty
    rank: int  # 1 = certain loader, 2 = frame/mixin, 3 = package heuristic
    attribution: str  # "loader_statement" | "frame_jar" | "mixin" | "package_heuristic"
    reason: str
    evidence_lines: list[tuple[int, str]] = field(default_factory=list)  # (line_num, line_str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribution": self.attribution,
            "certainty": self.certainty.value,
            "evidence_lines": [list(item) for item in self.evidence_lines],
            "mod_id": self.mod_id,
            "name": self.name,
            "rank": self.rank,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashSuspect:
        return cls(
            mod_id=data.get("mod_id"),
            name=data.get("name"),
            certainty=Certainty(data["certainty"]),
            rank=data["rank"],
            attribution=data["attribution"],
            reason=data["reason"],
            evidence_lines=[(int(e[0]), str(e[1])) for e in data.get("evidence_lines", [])],
        )


@dataclass(frozen=True)
class ModEntry:
    """A mod parsed from crash report / log mod tables."""

    id: str
    version: str | None = None
    file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "id": self.id,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModEntry:
        return cls(
            id=data["id"],
            version=data.get("version"),
            file=data.get("file"),
        )


@dataclass(frozen=True)
class CrashAnalysisResult:
    """Structured result of parsing and analyzing a crash log or report."""

    kind: CrashReportKind
    description: str | None = None
    mc_version: str | None = None
    java_version: str | None = None
    detected_loader: str | None = None
    root_cause: ExceptionInfo | None = None
    exceptions: list[ExceptionInfo] = field(default_factory=list)
    suspects: list[CrashSuspect] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    mods_listed: list[ModEntry] = field(default_factory=list)
    is_truncated: bool = False
    top_suspect: CrashSuspect | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "detected_loader": self.detected_loader,
            "exceptions": [e.to_dict() for e in self.exceptions],
            "findings": [f.to_dict() for f in self.findings],
            "is_truncated": self.is_truncated,
            "java_version": self.java_version,
            "kind": self.kind.value,
            "mc_version": self.mc_version,
            "mods_listed": [m.to_dict() for m in self.mods_listed],
            "root_cause": self.root_cause.to_dict() if self.root_cause is not None else None,
            "suspects": [s.to_dict() for s in self.suspects],
            "top_suspect": self.top_suspect.to_dict() if self.top_suspect is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashAnalysisResult:
        rc_data = data.get("root_cause")
        root_cause = ExceptionInfo.from_dict(rc_data) if rc_data is not None else None
        top_data = data.get("top_suspect")
        top_suspect = CrashSuspect.from_dict(top_data) if top_data is not None else None

        return cls(
            kind=CrashReportKind(data["kind"]),
            description=data.get("description"),
            mc_version=data.get("mc_version"),
            java_version=data.get("java_version"),
            detected_loader=data.get("detected_loader"),
            root_cause=root_cause,
            exceptions=[ExceptionInfo.from_dict(e) for e in data.get("exceptions", [])],
            suspects=[CrashSuspect.from_dict(s) for s in data.get("suspects", [])],
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            mods_listed=[ModEntry.from_dict(m) for m in data.get("mods_listed", [])],
            is_truncated=data.get("is_truncated", False),
            top_suspect=top_suspect,
        )


@dataclass(frozen=True)
class CrashRecord:
    """Stored crash report metadata and optional payload_json."""

    id: int
    profile_id: int | None
    imported_at: str
    source_name: str
    file_size: int
    sha256: str
    kind: str
    mc_version: str | None
    loader: str | None
    top_suspect: str | None
    error_count: int
    warning_count: int
    info_count: int
    payload_json: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_count": self.error_count,
            "file_size": self.file_size,
            "id": self.id,
            "imported_at": self.imported_at,
            "info_count": self.info_count,
            "kind": self.kind,
            "loader": self.loader,
            "mc_version": self.mc_version,
            "profile_id": self.profile_id,
            "sha256": self.sha256,
            "source_name": self.source_name,
            "top_suspect": self.top_suspect,
            "warning_count": self.warning_count,
        }
