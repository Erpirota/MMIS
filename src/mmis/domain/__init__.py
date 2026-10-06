"""Domain layer for MMIS.

Pure stdlib, no imports from other mmis packages.
"""

from mmis.domain.findings import (
    AMBIGUOUS_LOADER,
    AMBIGUOUS_MC_VERSION,
    CONFLICTING_MANIFEST,
    CORRUPT_JAR,
    INVALID_METADATA_JSON,
    INVALID_METADATA_TOML,
    INVALID_PACK_MANIFEST,
    INVALID_VERSION_RANGE,
    LEGACY_MOD_INFO,
    LENIENT_JSON_REPAIRED,
    NESTED_JAR_DEPTH_EXCEEDED,
    NESTED_JAR_SIZE_EXCEEDED,
    NO_METADATA,
    NOT_A_ZIP,
    PACK_MANIFEST_DETECTED,
    UNKNOWN_LOADER,
    UNRESOLVED_PLACEHOLDER,
)
from mmis.domain.models import (
    Certainty,
    Dependency,
    DependencyKind,
    Evidence,
    Finding,
    JarFile,
    Loader,
    ModInfo,
    ScanResult,
    Severity,
)
from mmis.domain.versions import ComparableVersion, VersionRange

__all__ = [
    "AMBIGUOUS_LOADER",
    "AMBIGUOUS_MC_VERSION",
    "CONFLICTING_MANIFEST",
    "CORRUPT_JAR",
    "Certainty",
    "ComparableVersion",
    "Dependency",
    "DependencyKind",
    "Evidence",
    "Finding",
    "INVALID_METADATA_JSON",
    "INVALID_METADATA_TOML",
    "INVALID_PACK_MANIFEST",
    "INVALID_VERSION_RANGE",
    "JarFile",
    "LEGACY_MOD_INFO",
    "LENIENT_JSON_REPAIRED",
    "Loader",
    "ModInfo",
    "NESTED_JAR_DEPTH_EXCEEDED",
    "NESTED_JAR_SIZE_EXCEEDED",
    "NO_METADATA",
    "NOT_A_ZIP",
    "PACK_MANIFEST_DETECTED",
    "ScanResult",
    "Severity",
    "UNKNOWN_LOADER",
    "UNRESOLVED_PLACEHOLDER",
    "VersionRange",
]
