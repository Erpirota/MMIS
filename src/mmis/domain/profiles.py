"""Domain models for modpack profiles and scan snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mmis.domain.models import ScanResult


class ProfileStatus(StrEnum):
    """Derived operational status of a modpack profile."""

    NEVER_SCANNED = "never_scanned"
    OK = "ok"
    WARNINGS = "warnings"
    ERRORS = "errors"
    PATH_MISSING = "path_missing"


@dataclass(frozen=True)
class Profile:
    """Configured modpack profile."""

    id: int | None
    name: str
    path: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        return cls(
            id=data.get("id"),
            name=data["name"],
            path=data["path"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(frozen=True)
class ScanSummary:
    """Lightweight metadata summary of a stored scan snapshot."""

    id: int
    profile_id: int
    scanned_at: str
    loader: str | None
    mc_version: str | None
    jar_count: int
    mod_count: int
    error_count: int
    warning_count: int
    info_count: int
    label: str | None = None
    pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_count": self.error_count,
            "id": self.id,
            "info_count": self.info_count,
            "jar_count": self.jar_count,
            "label": self.label,
            "loader": self.loader,
            "mc_version": self.mc_version,
            "mod_count": self.mod_count,
            "pinned": self.pinned,
            "profile_id": self.profile_id,
            "scanned_at": self.scanned_at,
            "warning_count": self.warning_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanSummary:
        return cls(
            id=data["id"],
            profile_id=data["profile_id"],
            scanned_at=data["scanned_at"],
            loader=data.get("loader"),
            mc_version=data.get("mc_version"),
            jar_count=data["jar_count"],
            mod_count=data["mod_count"],
            error_count=data["error_count"],
            warning_count=data["warning_count"],
            info_count=data["info_count"],
            label=data.get("label"),
            pinned=bool(data.get("pinned", False)),
        )


@dataclass(frozen=True)
class ScanRecord:
    """Full scan snapshot containing summary columns and the parsed ScanResult."""

    summary: ScanSummary
    result: ScanResult
    analysis: Any = None

    def to_dict(self) -> dict[str, Any]:
        data = self.summary.to_dict()
        data["payload"] = self.result.to_dict()
        if self.analysis is not None:
            data["analysis"] = self.analysis.to_dict() if hasattr(self.analysis, "to_dict") else self.analysis
        return data


@dataclass(frozen=True)
class ProfileSummary:
    """Summary view of a profile including its derived operational status."""

    id: int
    name: str
    path: str
    status: ProfileStatus
    last_scanned_at: str | None = None
    last_loader: str | None = None
    last_mc_version: str | None = None
    mod_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None
    scan_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_count": self.error_count,
            "id": self.id,
            "last_loader": self.last_loader,
            "last_mc_version": self.last_mc_version,
            "last_scanned_at": self.last_scanned_at,
            "mod_count": self.mod_count,
            "name": self.name,
            "path": self.path,
            "scan_count": self.scan_count,
            "status": self.status.value,
            "warning_count": self.warning_count,
        }
