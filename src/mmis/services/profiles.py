"""Service use cases for managing modpack profiles and scan snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from mmis.analysis.crashlog import MAX_LOG_SIZE, analyze_crash
from mmis.analysis.diff import FindingDiff, ModDiff, ScanDiff, diff_scans
from mmis.analysis.engine import AnalysisReport, analyze_pack
from mmis.domain.crash import CrashAnalysisResult, CrashRecord
from mmis.domain.errors import (
    DuplicateProfileError,
    ProfileNotFoundError,
    ProfilePathMissingError,
    ScanNotFoundError,
    StorageError,
)
from mmis.domain.models import ScanResult, Severity
from mmis.domain.profiles import (
    Profile,
    ProfileStatus,
    ProfileSummary,
    ScanRecord,
    ScanSummary,
)
from mmis.minecraft.scanner import scan
from mmis.storage.repository import ProfileRepository


class ProfileService:
    """Orchestrates modpack profile operations, scanning, and history."""

    def __init__(
        self,
        repo: ProfileRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now_iso(self) -> str:
        return self.clock().isoformat()

    @staticmethod
    def _validate_name(name: str) -> str:
        stripped = name.strip()
        if not stripped:
            raise ValueError("Profile name cannot be empty or whitespace only.")
        if len(stripped) > 100:
            raise ValueError(f"Profile name '{stripped}' exceeds maximum length of 100 characters.")
        return stripped

    def add_profile(self, name: str, path: str | Path) -> Profile:
        """Create a new profile pointing to a modpack filesystem directory."""
        clean_name = self._validate_name(name)
        existing = self.repo.get_profile_by_name(clean_name)
        if existing is not None:
            raise DuplicateProfileError(f"A profile named '{clean_name}' already exists.")

        abs_path = os.path.abspath(str(path))
        now = self._now_iso()
        return self.repo.create_profile(clean_name, abs_path, now, now)

    def get_profile(self, name: str) -> Profile:
        """Retrieve a profile by case-insensitive name."""
        clean_name = self._validate_name(name)
        profile = self.repo.get_profile_by_name(clean_name)
        if profile is None:
            raise ProfileNotFoundError(f"Profile '{clean_name}' was not found.")
        return profile

    def rename_profile(self, old_name: str, new_name: str) -> Profile:
        """Rename an existing profile."""
        profile = self.get_profile(old_name)
        clean_new = self._validate_name(new_name)

        if clean_new.casefold() != profile.name.casefold():
            existing = self.repo.get_profile_by_name(clean_new)
            if existing is not None:
                raise DuplicateProfileError(f"A profile named '{clean_new}' already exists.")

        now = self._now_iso()
        self.repo.rename_profile(profile.id, clean_new, now)
        return self.get_profile(clean_new)

    def update_profile_path(self, name: str, new_path: str | Path) -> Profile:
        """Update the directory path for an existing profile."""
        profile = self.get_profile(name)
        abs_path = os.path.abspath(str(new_path))
        now = self._now_iso()
        self.repo.update_profile_path(profile.id, abs_path, now)
        return self.get_profile(name)

    def remove_profile(self, name: str) -> bool:
        """Delete an existing profile and all associated scans."""
        profile = self.get_profile(name)
        return self.repo.delete_profile(profile.id)

    @staticmethod
    def derive_status(profile: Profile, latest_scan: ScanSummary | None) -> ProfileStatus:
        """Derive the current operational status of a profile."""
        if not os.path.exists(profile.path) or not os.path.isdir(profile.path):
            return ProfileStatus.PATH_MISSING
        if latest_scan is None:
            return ProfileStatus.NEVER_SCANNED
        if latest_scan.error_count > 0:
            return ProfileStatus.ERRORS
        if latest_scan.warning_count > 0:
            return ProfileStatus.WARNINGS
        return ProfileStatus.OK

    def list_profiles(self) -> list[ProfileSummary]:
        """List all profiles along with their derived operational status and scan metrics."""
        profiles = self.repo.list_profiles()
        summaries: list[ProfileSummary] = []
        for p in profiles:
            latest = self.repo.get_latest_scan_for_profile(p.id)
            count = self.repo.get_scan_count_for_profile(p.id)
            status = self.derive_status(p, latest)
            summaries.append(
                ProfileSummary(
                    id=p.id,
                    name=p.name,
                    path=p.path,
                    status=status,
                    last_scanned_at=latest.scanned_at if latest else None,
                    last_loader=latest.loader if latest else None,
                    last_mc_version=latest.mc_version if latest else None,
                    mod_count=latest.mod_count if latest else None,
                    error_count=latest.error_count if latest else None,
                    warning_count=latest.warning_count if latest else None,
                    scan_count=count,
                )
            )
        return summaries

    def scan_profile(
        self,
        name: str,
        label: str | None = None,
        retention_limit: int = 20,
    ) -> ScanRecord:
        """Scan the filesystem directory of a profile and persist a new snapshot."""
        profile = self.get_profile(name)

        if not os.path.exists(profile.path) or not os.path.isdir(profile.path):
            raise ProfilePathMissingError(
                f"Profile directory '{profile.path}' does not exist or is not a directory."
            )

        scan_res = scan(profile.path)
        analysis_rep = analyze_pack(scan_res)
        now = self._now_iso()

        err_count = sum(1 for f in scan_res.findings if f.severity == Severity.ERROR) + analysis_rep.severity_counts.get("error", 0)
        warn_count = sum(1 for f in scan_res.findings if f.severity == Severity.WARNING) + analysis_rep.severity_counts.get("warning", 0)
        info_count = sum(1 for f in scan_res.findings if f.severity == Severity.INFO) + analysis_rep.severity_counts.get("info", 0)

        payload_dict = scan_res.to_dict()
        payload_json = json.dumps(payload_dict, indent=2, sort_keys=True)
        analysis_json = json.dumps(analysis_rep.to_dict(), indent=2, sort_keys=True)

        summary = ScanSummary(
            id=0,
            profile_id=profile.id,
            scanned_at=now,
            loader=scan_res.detected_loader.value if scan_res.detected_loader else None,
            mc_version=scan_res.detected_mc_version,
            jar_count=len(scan_res.jars),
            mod_count=len(scan_res.mods),
            error_count=err_count,
            warning_count=warn_count,
            info_count=info_count,
            label=label,
            pinned=False,
        )

        scan_id = self.repo.insert_scan(
            profile_id=profile.id,
            summary=summary,
            payload_json=payload_json,
            analysis_json=analysis_json,
            retention_limit=retention_limit,
        )
        self.repo.update_profile_path(profile.id, profile.path, now)

        persisted_summary = ScanSummary(
            id=scan_id,
            profile_id=profile.id,
            scanned_at=summary.scanned_at,
            loader=summary.loader,
            mc_version=summary.mc_version,
            jar_count=summary.jar_count,
            mod_count=summary.mod_count,
            error_count=summary.error_count,
            warning_count=summary.warning_count,
            info_count=summary.info_count,
            label=summary.label,
            pinned=summary.pinned,
        )
        return ScanRecord(summary=persisted_summary, result=scan_res, analysis=analysis_rep)

    def get_history(self, name: str, limit: int | None = None) -> list[ScanSummary]:
        """Get scan snapshots for a profile ordered newest first."""
        profile = self.get_profile(name)
        return self.repo.list_scans_for_profile(profile.id, limit=limit)

    def get_scan(self, name: str, scan_id: int) -> ScanResult:
        """Fetch and deserialize a stored ScanResult snapshot."""
        profile = self.get_profile(name)
        rec = self.repo.get_scan(scan_id)
        if rec is None or rec[0].profile_id != profile.id:
            raise ScanNotFoundError(f"Scan ID {scan_id} does not exist for profile '{name}'.")

        summary, payload_json = rec[0], rec[1]
        try:
            data = json.loads(payload_json)
            return ScanResult.from_dict(data)
        except Exception as exc:
            raise StorageError(f"Failed to decode payload for scan ID {scan_id}: {exc}") from exc

    def get_scan_record(self, name: str, scan_id: int) -> ScanRecord:
        """Fetch and deserialize a stored ScanRecord including AnalysisReport."""
        profile = self.get_profile(name)
        rec = self.repo.get_scan(scan_id)
        if rec is None or rec[0].profile_id != profile.id:
            raise ScanNotFoundError(f"Scan ID {scan_id} does not exist for profile '{name}'.")

        summary, payload_json = rec[0], rec[1]
        try:
            data = json.loads(payload_json)
            scan_res = ScanResult.from_dict(data)
            analysis_json = getattr(rec, "analysis_json", None)
            if analysis_json:
                analysis_dict = json.loads(analysis_json)
                analysis_rep = AnalysisReport.from_dict(analysis_dict)
            else:
                # Recomputed on read for old rows
                analysis_rep = analyze_pack(scan_res)
            return ScanRecord(summary=summary, result=scan_res, analysis=analysis_rep)
        except Exception as exc:
            raise StorageError(f"Failed to decode payload for scan ID {scan_id}: {exc}") from exc

    def set_scan_label(self, name: str, scan_id: int, label: str | None) -> None:
        """Set or update the label on a scan."""
        profile = self.get_profile(name)
        rec = self.repo.get_scan(scan_id)
        if rec is None or rec[0].profile_id != profile.id:
            raise ScanNotFoundError(f"Scan ID {scan_id} does not exist for profile '{name}'.")
        self.repo.set_scan_label(scan_id, label)

    def set_scan_pinned(self, name: str, scan_id: int, pinned: bool) -> None:
        """Pin or unpin a scan to prevent retention pruning."""
        profile = self.get_profile(name)
        rec = self.repo.get_scan(scan_id)
        if rec is None or rec[0].profile_id != profile.id:
            raise ScanNotFoundError(f"Scan ID {scan_id} does not exist for profile '{name}'.")
        self.repo.set_scan_pinned(scan_id, pinned)

    def diff_scans(
        self,
        name: str,
        scan_a_id: int | None = None,
        scan_b_id: int | None = None,
    ) -> ScanDiff:
        """Diff two scans of a profile. Defaults to comparing previous vs latest scan."""
        profile = self.get_profile(name)

        if scan_a_id is None and scan_b_id is None:
            history = self.repo.list_scans_for_profile(profile.id, limit=2)
            if len(history) < 2:
                raise ValueError(
                    f"Profile '{name}' has fewer than two scans to diff (found {len(history)})."
                )
            # history is newest first: history[1] is previous (A), history[0] is latest (B)
            scan_a_id = history[1].id
            scan_b_id = history[0].id
        elif scan_a_id is None or scan_b_id is None:
            raise ValueError("Must provide either both scan IDs to diff or neither.")

        scan_a = self.get_scan(name, scan_a_id)
        scan_b = self.get_scan(name, scan_b_id)
        return diff_scans(scan_a, scan_b)

    def import_crash(
        self,
        file_path_or_content: str | Path | bytes,
        profile_name: str | None = None,
        source_name: str = "",
    ) -> CrashRecord:
        """Import and analyze a crash report or log, optionally linked to a profile."""
        profile_id: int | None = None
        scan_context: ScanResult | None = None

        if profile_name:
            profile = self.get_profile(profile_name)
            profile_id = profile.id
            latest_summary = self.repo.get_latest_scan_for_profile(profile.id)
            if latest_summary:
                scan_context = self.get_scan(profile.name, latest_summary.id)

        if isinstance(file_path_or_content, (str, Path)) and os.path.exists(str(file_path_or_content)) and os.path.isfile(str(file_path_or_content)):
            path_obj = Path(file_path_or_content)
            src_name = source_name or path_obj.name
            file_size = path_obj.stat().st_size
            h = hashlib.sha256()
            with open(path_obj, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            sha256_hash = h.hexdigest()
            with open(path_obj, "rb") as fh:
                raw_bytes = fh.read(MAX_LOG_SIZE + 1)
        elif isinstance(file_path_or_content, bytes):
            raw_bytes = file_path_or_content
            file_size = len(raw_bytes)
            sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
            src_name = source_name or "crash.log"
        else:
            raw_bytes = str(file_path_or_content).encode("utf-8")
            file_size = len(raw_bytes)
            sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
            src_name = source_name or "crash.log"

        analysis = analyze_crash(raw_bytes, scan=scan_context, source_name=src_name)

        err_count = sum(1 for f in analysis.findings if f.severity == Severity.ERROR)
        warn_count = sum(1 for f in analysis.findings if f.severity == Severity.WARNING)
        info_count = sum(1 for f in analysis.findings if f.severity == Severity.INFO)
        top_suspect_str = analysis.top_suspect.mod_id if analysis.top_suspect else None

        payload_dict = analysis.to_dict()
        payload_json = json.dumps(payload_dict, indent=2, sort_keys=True)
        now = self._now_iso()

        crash_id = self.repo.insert_crash_report(
            profile_id=profile_id,
            imported_at=now,
            source_name=src_name,
            file_size=file_size,
            sha256=sha256_hash,
            kind=analysis.kind.value,
            mc_version=analysis.mc_version,
            loader=analysis.detected_loader,
            top_suspect=top_suspect_str,
            error_count=err_count,
            warning_count=warn_count,
            info_count=info_count,
            payload_json=payload_json,
        )

        return CrashRecord(
            id=crash_id,
            profile_id=profile_id,
            imported_at=now,
            source_name=src_name,
            file_size=file_size,
            sha256=sha256_hash,
            kind=analysis.kind.value,
            mc_version=analysis.mc_version,
            loader=analysis.detected_loader,
            top_suspect=top_suspect_str,
            error_count=err_count,
            warning_count=warn_count,
            info_count=info_count,
            payload_json=payload_json,
        )

    def list_crashes(self, profile_name: str | None = None, limit: int | None = None) -> list[CrashRecord]:
        """List stored crash reports, optionally filtered by profile name."""
        profile_id: int | None = None
        if profile_name:
            profile = self.get_profile(profile_name)
            profile_id = profile.id
        return self.repo.list_crash_reports(profile_id=profile_id, limit=limit)

    def get_crash(self, crash_id: int) -> tuple[CrashRecord, CrashAnalysisResult]:
        """Retrieve a crash report record and parsed CrashAnalysisResult by ID."""
        record = self.repo.get_crash_report(crash_id)
        if record is None:
            raise ValueError(f"Crash report ID {crash_id} was not found.")
        payload_dict = json.loads(record.payload_json or "{}")
        analysis = CrashAnalysisResult.from_dict(payload_dict)
        return record, analysis

    def delete_crash(self, crash_id: int) -> bool:
        """Delete a crash report by ID."""
        return self.repo.delete_crash_report(crash_id)

    def get_latest_scan_record(self, name: str) -> ScanRecord | None:
        """Retrieve the most recent ScanRecord for a profile, or None if never scanned."""
        profile = self.get_profile(name)
        summary = self.repo.get_latest_scan_for_profile(profile.id)
        if summary is None:
            return None
        return self.get_scan_record(name, summary.id)

    def get_latest_crash(self, name: str) -> tuple[CrashRecord, CrashAnalysisResult] | None:
        """Retrieve the most recently imported crash report for a profile, or None."""
        crashes = self.list_crashes(profile_name=name, limit=1)
        if not crashes:
            return None
        return self.get_crash(crashes[0].id)


def create_profile_service(data_dir: str | Path | None = None) -> ProfileService:
    """Factory creating a ProfileService using default or custom data directory."""
    from mmis.services.paths import get_db_path
    db_path = get_db_path(data_dir)
    repo = ProfileRepository(db_path)
    return ProfileService(repo)

