"""Domain models and pure stdlib dataclasses for MMIS.

No external dependencies, pure stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mmis.domain.versions import VersionRange


class Severity(StrEnum):
    """Finding severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Certainty(StrEnum):
    """Finding certainty levels."""

    CERTAIN = "certain"
    POSSIBLE = "possible"
    INSUFFICIENT_DATA = "insufficient_data"


class Loader(StrEnum):
    """Minecraft mod loader families."""

    FABRIC = "fabric"
    QUILT = "quilt"
    FORGE = "forge"
    NEOFORGE = "neoforge"


class DependencyKind(StrEnum):
    """Semantic classification of mod dependency."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    BREAKS = "breaks"
    CONFLICTS = "conflicts"


@dataclass(frozen=True)
class Evidence:
    """Structured evidence pointing to specific file, field, or raw value."""

    file: str
    field: str | None = None
    raw_value: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "file": self.file,
            "message": self.message,
            "raw_value": self.raw_value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            file=data["file"],
            field=data.get("field"),
            raw_value=data.get("raw_value"),
            message=data.get("message"),
        )


@dataclass(frozen=True)
class Finding:
    """Structured diagnostic finding with evidence."""

    code: str
    severity: Severity
    certainty: Certainty
    message: str
    evidence: list[Evidence]
    related_mod_ids: list[str] = field(default_factory=list)
    suggestion: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(f"Finding '{self.code}' must have at least one Evidence item.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "certainty": self.certainty.value,
            "code": self.code,
            "evidence": [e.to_dict() for e in self.evidence],
            "message": self.message,
            "related_mod_ids": sorted(self.related_mod_ids),
            "severity": self.severity.value,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            code=data["code"],
            severity=Severity(data["severity"]),
            certainty=Certainty(data["certainty"]),
            message=data["message"],
            evidence=[Evidence.from_dict(e) for e in data.get("evidence", [])],
            related_mod_ids=list(data.get("related_mod_ids", [])),
            suggestion=data.get("suggestion"),
        )


@dataclass(frozen=True)
class Dependency:
    """Declared dependency of a mod."""

    mod_id: str
    version_range: VersionRange | None = None
    kind: DependencyKind = DependencyKind.REQUIRED
    declared_as: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "declared_as": self.declared_as,
            "kind": self.kind.value,
            "mod_id": self.mod_id,
            "version_range": self.version_range.to_dict() if self.version_range is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Dependency:
        vr_data = data.get("version_range")
        vr = VersionRange.from_dict(vr_data) if vr_data is not None else None
        return cls(
            mod_id=data["mod_id"],
            version_range=vr,
            kind=DependencyKind(data.get("kind", DependencyKind.REQUIRED.value)),
            declared_as=data.get("declared_as"),
        )


@dataclass(frozen=True)
class ModInfo:
    """Metadata extracted for a single mod."""

    id: str
    name: str | None = None
    version: str | None = None
    loaders: list[Loader] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    jar_path: str = ""
    metadata_file: str = ""
    description: str | None = None
    authors: list[str] = field(default_factory=list)
    license: str | None = None
    environment: str | None = None
    parent_jar: str | None = None
    related_files: list[str] = field(default_factory=list)
    is_nested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "authors": sorted(self.authors),
            "description": self.description,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "environment": self.environment,
            "id": self.id,
            "is_nested": self.is_nested,
            "jar_path": self.jar_path,
            "license": self.license,
            "loaders": [loader.value for loader in self.loaders],
            "metadata_file": self.metadata_file,
            "name": self.name,
            "parent_jar": self.parent_jar,
            "provides": sorted(self.provides),
            "related_files": sorted(self.related_files),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModInfo:
        return cls(
            id=data["id"],
            name=data.get("name"),
            version=data.get("version"),
            loaders=[Loader(l) for l in data.get("loaders", [])],
            provides=list(data.get("provides", [])),
            dependencies=[Dependency.from_dict(d) for d in data.get("dependencies", [])],
            jar_path=data.get("jar_path", ""),
            metadata_file=data.get("metadata_file", ""),
            description=data.get("description"),
            authors=list(data.get("authors", [])),
            license=data.get("license"),
            environment=data.get("environment"),
            parent_jar=data.get("parent_jar"),
            related_files=list(data.get("related_files", [])),
            is_nested=data.get("is_nested", False),
        )


@dataclass(frozen=True)
class JarFile:
    """Information about a physical or nested jar file."""

    path: str
    size_bytes: int
    sha256: str
    is_disabled: bool = False
    mods: list[str] = field(default_factory=list)
    is_nested: bool = False
    parent_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_disabled": self.is_disabled,
            "is_nested": self.is_nested,
            "mods": sorted(self.mods),
            "parent_path": self.parent_path,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JarFile:
        return cls(
            path=data["path"],
            size_bytes=data["size_bytes"],
            sha256=data["sha256"],
            is_disabled=data.get("is_disabled", False),
            mods=list(data.get("mods", [])),
            is_nested=data.get("is_nested", False),
            parent_path=data.get("parent_path"),
        )


@dataclass(frozen=True)
class ScanResult:
    """Overall outcome of scanning a modpack directory."""

    scanned_path: str
    mods_dir: str
    jars: list[JarFile] = field(default_factory=list)
    mods: list[ModInfo] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    detected_loader: Loader | None = None
    detected_mc_version: str | None = None
    mc_constraints: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # Stable sorting for determinism
        sorted_jars = sorted(self.jars, key=lambda j: j.path)
        sorted_mods = sorted(self.mods, key=lambda m: (m.id, m.jar_path))
        sorted_findings = sorted(
            self.findings,
            key=lambda f: (
                f.code,
                f.message,
                tuple((e.file, e.field or "", e.raw_value or "", e.message or "") for e in f.evidence),
            ),
        )
        sorted_mc_constraints = {k: self.mc_constraints[k] for k in sorted(self.mc_constraints.keys())}

        return {
            "detected_loader": self.detected_loader.value if self.detected_loader is not None else None,
            "detected_mc_version": self.detected_mc_version,
            "findings": [f.to_dict() for f in sorted_findings],
            "jars": [j.to_dict() for j in sorted_jars],
            "mc_constraints": sorted_mc_constraints,
            "mods": [m.to_dict() for m in sorted_mods],
            "mods_dir": self.mods_dir,
            "scanned_path": self.scanned_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScanResult:
        dl_str = data.get("detected_loader")
        detected_loader = Loader(dl_str) if dl_str is not None else None

        return cls(
            scanned_path=data.get("scanned_path", ""),
            mods_dir=data.get("mods_dir", ""),
            jars=[JarFile.from_dict(j) for j in data.get("jars", [])],
            mods=[ModInfo.from_dict(m) for m in data.get("mods", [])],
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            detected_loader=detected_loader,
            detected_mc_version=data.get("detected_mc_version"),
            mc_constraints=dict(data.get("mc_constraints", {})),
        )
