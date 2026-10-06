"""Domain-specific typed exceptions for MMIS."""

from __future__ import annotations


class MMISError(Exception):
    """Base exception for all domain and service errors in MMIS."""


class ProfileNotFoundError(MMISError):
    """Raised when a requested profile does not exist."""


class DuplicateProfileError(MMISError):
    """Raised when attempting to create or rename a profile with an existing name."""


class ProfilePathMissingError(MMISError):
    """Raised when scanning a profile whose filesystem directory does not exist."""


class ScanNotFoundError(MMISError):
    """Raised when a requested scan ID does not exist or does not belong to the profile."""


class StorageError(MMISError):
    """Raised when a database or serialization/deserialization failure occurs."""


class SchemaTooNewError(StorageError):
    """Raised when opening a database with a schema version higher than supported."""


class BackupInvalidError(MMISError):
    """Raised when a backup archive is corrupt, invalid, unsafe, or incompatible."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or f"Backup invalid: {reason}")


class ExportTargetExistsError(MMISError):
    """Raised when attempting to export to an existing file without force."""


class SnapshotInvalidError(MMISError):
    """Raised when an imported scan snapshot is invalid or corrupted."""
