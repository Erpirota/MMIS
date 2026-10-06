"""Headless command-line interface for MMIS.

Provides 'scan <dir> [--json]' and 'profile ...' commands with deterministic output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from mmis.analysis.crashlog import analyze_crash
from mmis.analysis.diff import diff_scans
from mmis.analysis.engine import analyze_pack
from mmis.domain.errors import (
    BackupInvalidError,
    DuplicateProfileError,
    ExportTargetExistsError,
    ProfileNotFoundError,
    ProfilePathMissingError,
    ScanNotFoundError,
    SchemaTooNewError,
    SnapshotInvalidError,
    StorageError,
)
from mmis.domain.models import Severity
from mmis.minecraft.scanner import scan
from mmis.services.backup import create_backup, restore_backup, verify_backup
from mmis.services.export import (
    export_report_html,
    export_report_json,
    export_scan_json,
    import_scan_json,
)
from mmis.services.paths import get_db_path
from mmis.services.profiles import ProfileService
from mmis.services.report import build_diagnostic_report
from mmis.storage.repository import ProfileRepository


def format_human_summary(result_dict: dict) -> str:
    """Format scan result as a clean human-readable summary."""
    lines: list[str] = [
        "=== MMIS Modpack Scan Summary ===",
        f"Scanned Directory : {result_dict.get('scanned_path') or '.'}",
        f"Detected Loader   : {result_dict.get('detected_loader') or 'None'}",
        f"Detected MC Ver   : {result_dict.get('detected_mc_version') or 'None'}",
    ]

    jars = result_dict.get("jars", [])
    enabled_jars = sum(1 for j in jars if not j.get("is_disabled"))
    disabled_jars = sum(1 for j in jars if j.get("is_disabled"))
    lines.append(f"Total Jars        : {len(jars)} (enabled: {enabled_jars}, disabled: {disabled_jars})")

    mods = result_dict.get("mods", [])
    lines.append(f"Total Mods        : {len(mods)}")

    mc_constraints = result_dict.get("mc_constraints", {})
    if mc_constraints:
        c_desc = ", ".join(f"'{k}': {v}" for k, v in mc_constraints.items())
        lines.append(f"MC Constraints    : {c_desc}")

    findings = result_dict.get("findings", [])
    err_count = sum(1 for f in findings if f.get("severity") == Severity.ERROR.value)
    warn_count = sum(1 for f in findings if f.get("severity") == Severity.WARNING.value)
    info_count = sum(1 for f in findings if f.get("severity") == Severity.INFO.value)
    lines.append(f"Total Findings    : {len(findings)} (errors: {err_count}, warnings: {warn_count}, info: {info_count})")

    if findings:
        lines.append("\nFindings List:")
        for f in findings:
            sev = f.get("severity", "").upper()
            code = f.get("code", "")
            msg = f.get("message", "")
            evidence = f.get("evidence", [])
            file_hint = f" [{evidence[0].get('file')}]" if evidence and evidence[0].get("file") else ""
            lines.append(f"  * [{sev}] {code}: {msg}{file_hint}")

    return "\n".join(lines)


def _get_profile_service(data_dir: str | None = None) -> ProfileService:
    db_path = get_db_path(data_dir)
    repo = ProfileRepository(db_path)
    return ProfileService(repo)


def _build_parser() -> argparse.ArgumentParser:
    common_options = argparse.ArgumentParser(add_help=False)
    common_options.add_argument(
        "--data-dir",
        dest="data_dir",
        default=argparse.SUPPRESS,
        help="Path to MMIS data directory (overrides MMIS_DATA_DIR)",
    )
    common_options.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="Output deterministic JSON format",
    )

    parser = argparse.ArgumentParser(
        prog="mmis",
        description="Minecraft Mod Intelligence Studio - Offline Modpack Scanner & Profile Manager",
        parents=[common_options],
        exit_on_error=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    # scan <dir>
    scan_parser = subparsers.add_parser("scan", parents=[common_options], help="Scan a modpack directory")
    scan_parser.add_argument("dir", help="Path to modpack directory to scan")

    # profile <subcommand>
    profile_parser = subparsers.add_parser(
        "profile",
        parents=[common_options],
        help="Manage modpack profiles and scan history",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_action", required=False)

    # profile add <name> <path>
    add_p = profile_subparsers.add_parser("add", parents=[common_options], help="Add a new modpack profile")
    add_p.add_argument("name", help="Profile name")
    add_p.add_argument("path", help="Path to modpack root directory")

    # profile list
    profile_subparsers.add_parser("list", parents=[common_options], help="List all configured profiles")

    # profile remove <name>
    remove_p = profile_subparsers.add_parser("remove", parents=[common_options], help="Remove a profile")
    remove_p.add_argument("name", help="Profile name")

    # profile rename <old> <new>
    rename_p = profile_subparsers.add_parser("rename", parents=[common_options], help="Rename a profile")
    rename_p.add_argument("old_name", help="Current profile name")
    rename_p.add_argument("new_name", help="New profile name")

    # profile scan <name> [--label TEXT]
    scan_p = profile_subparsers.add_parser("scan", parents=[common_options], help="Scan a profile and save snapshot")
    scan_p.add_argument("name", help="Profile name")
    scan_p.add_argument("--label", dest="label", default=None, help="Optional label for the scan")

    # profile history <name>
    hist_p = profile_subparsers.add_parser("history", parents=[common_options], help="View scan history of a profile")
    hist_p.add_argument("name", help="Profile name")
    hist_p.add_argument("--limit", type=int, default=None, help="Limit number of scans")

    # profile diff <name> [<scanA> <scanB>] [--file SNAPSHOT.json]
    diff_p = profile_subparsers.add_parser("diff", parents=[common_options], help="Diff two scans of a profile or compare against a snapshot file")
    diff_p.add_argument("name", help="Profile name")
    diff_p.add_argument(
        "scans",
        nargs="*",
        type=int,
        help="Optional scan IDs to diff (default: previous vs latest, or 1 scan ID if --file is used)",
    )
    diff_p.add_argument("--file", dest="diff_file", default=None, help="Compare stored scan against an exported snapshot JSON file")

    # profile export-scan <name> <scanId> --out FILE [--force]
    exp_scan_p = profile_subparsers.add_parser("export-scan", parents=[common_options], help="Export a scan snapshot as JSON")
    exp_scan_p.add_argument("name", help="Profile name")
    exp_scan_p.add_argument("scan_id", type=int, help="Scan ID to export")
    exp_scan_p.add_argument("--out", required=True, help="Destination JSON file path")
    exp_scan_p.add_argument("--force", action="store_true", default=False, help="Overwrite target file if it exists")

    # profile pin <name> <scanId>
    pin_p = profile_subparsers.add_parser("pin", parents=[common_options], help="Pin a scan to prevent retention pruning")
    pin_p.add_argument("name", help="Profile name")
    pin_p.add_argument("scan_id", type=int, help="Scan ID to pin")

    # profile unpin <name> <scanId>
    unpin_p = profile_subparsers.add_parser("unpin", parents=[common_options], help="Unpin a scan")
    unpin_p.add_argument("name", help="Profile name")
    unpin_p.add_argument("scan_id", type=int, help="Scan ID to unpin")

    # report <profile> [--scan ID] --format json|html --out FILE [--force]
    report_parser = subparsers.add_parser(
        "report",
        parents=[common_options],
        help="Generate a comprehensive diagnostic report (JSON or standalone HTML)",
    )
    report_parser.add_argument("profile", help="Profile name")
    report_parser.add_argument("--scan", type=int, default=None, help="Optional scan ID (defaults to latest)")
    report_parser.add_argument("--format", choices=["json", "html"], required=True, help="Output format (json or html)")
    report_parser.add_argument("--out", required=True, help="Output file destination")
    report_parser.add_argument("--force", action="store_true", default=False, help="Overwrite existing output file")

    # backup <subcommand>
    backup_parser = subparsers.add_parser(
        "backup",
        parents=[common_options],
        help="Create, verify, and restore full application backups",
    )
    backup_subparsers = backup_parser.add_subparsers(dest="backup_action", required=False)

    # backup create --out FILE [--force]
    b_create = backup_subparsers.add_parser("create", parents=[common_options], help="Create a backup zip archive")
    b_create.add_argument("--out", required=True, help="Destination zip file path")
    b_create.add_argument("--force", action="store_true", default=False, help="Overwrite existing file")

    # backup verify FILE
    b_verify = backup_subparsers.add_parser("verify", parents=[common_options], help="Verify backup archive integrity")
    b_verify.add_argument("file", help="Path to backup zip archive")

    # backup restore FILE [--yes] [--force]
    b_restore = backup_subparsers.add_parser("restore", parents=[common_options], help="Restore from backup archive")
    b_restore.add_argument("file", help="Path to backup zip archive")
    b_restore.add_argument("--yes", action="store_true", default=False, help="Confirm restore non-interactively without prompt")
    b_restore.add_argument("--force", action="store_true", default=False, help="Force overwrite during restore")

    # analyze <dir>
    analyze_parser = subparsers.add_parser(
        "analyze",
        parents=[common_options],
        help="Analyze modpack dependencies and conflicts",
    )
    analyze_parser.add_argument("dir", help="Path to modpack directory to analyze")

    # crash <subcommand>
    crash_parser = subparsers.add_parser(
        "crash",
        parents=[common_options],
        help="Analyze and manage crash logs",
    )
    crash_subparsers = crash_parser.add_subparsers(dest="crash_action", required=False)

    # crash analyze <file> [--pack <dir> | --profile <name>] [--save]
    cr_analyze = crash_subparsers.add_parser("analyze", parents=[common_options], help="Analyze a crash report or log file")
    cr_analyze.add_argument("file", help="Path to crash report or log file")
    cr_analyze.add_argument("--pack", dest="pack", default=None, help="Optional modpack directory for mod attribution")
    cr_analyze.add_argument("--profile", dest="profile", default=None, help="Optional profile name for mod attribution")
    cr_analyze.add_argument("--save", action="store_true", default=False, help="Save analysis into database")

    # crash list [--profile <name>]
    cr_list = crash_subparsers.add_parser("list", parents=[common_options], help="List stored crash reports")
    cr_list.add_argument("--profile", dest="profile", default=None, help="Filter by profile name")

    # crash show <id>
    cr_show = crash_subparsers.add_parser("show", parents=[common_options], help="Show stored crash report details")
    cr_show.add_argument("id", type=int, help="Crash report ID")

    # crash delete <id>
    cr_del = crash_subparsers.add_parser("delete", parents=[common_options], help="Delete a stored crash report")
    cr_del.add_argument("id", type=int, help="Crash report ID")

    # gui [--data-dir DIR]
    subparsers.add_parser("gui", parents=[common_options], help="Launch MMIS desktop GUI")

    return parser


def _handle_scan(args: argparse.Namespace) -> int:
    target_dir = args.dir
    json_output = getattr(args, "json_output", False)
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        print(f"Error: Target path '{target_dir}' does not exist or is not a directory.", file=sys.stderr)
        return 2

    try:
        scan_res = scan(target_dir)
        res_dict = scan_res.to_dict()

        if json_output:
            json_text = json.dumps(res_dict, indent=2, sort_keys=True)
            print(json_text)
        else:
            print(format_human_summary(res_dict))

        return 0
    except Exception as exc:
        print(f"Unexpected scan error: {exc}", file=sys.stderr)
        return 2


def _handle_profile(args: argparse.Namespace) -> int:
    action = getattr(args, "profile_action", None)
    if not action:
        print("Usage error: missing profile subcommand (e.g. add, list, remove, scan, history, diff, pin, unpin, rename).", file=sys.stderr)
        return 2

    data_dir = getattr(args, "data_dir", None)
    json_output = getattr(args, "json_output", False)

    try:
        service = _get_profile_service(data_dir)
    except SchemaTooNewError as exc:
        print(f"Schema error: {exc}", file=sys.stderr)
        return 1
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Operational error initializing storage: {exc}", file=sys.stderr)
        return 1

    try:
        if action == "add":
            profile = service.add_profile(args.name, args.path)
            if json_output:
                print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"Profile '{profile.name}' added successfully -> {profile.path}")
            return 0

        elif action == "list":
            profiles = service.list_profiles()
            if json_output:
                print(json.dumps([p.to_dict() for p in profiles], indent=2, sort_keys=True))
            else:
                if not profiles:
                    print("No profiles configured.")
                else:
                    lines = [
                        f"{'NAME':<20} {'STATUS':<15} {'MC VER':<12} {'LOADER':<10} {'MODS':<6} {'ERRS':<6} PATH",
                        "-" * 85,
                    ]
                    for p in profiles:
                        mc = p.last_mc_version or "-"
                        loader = p.last_loader or "-"
                        mods = str(p.mod_count) if p.mod_count is not None else "-"
                        errs = str(p.error_count) if p.error_count is not None else "-"
                        lines.append(
                            f"{p.name:<20} {p.status.value:<15} {mc:<12} {loader:<10} {mods:<6} {errs:<6} {p.path}"
                        )
                    print("\n".join(lines))
            return 0

        elif action == "remove":
            service.remove_profile(args.name)
            if json_output:
                print(json.dumps({"deleted": True, "name": args.name}, indent=2, sort_keys=True))
            else:
                print(f"Profile '{args.name}' removed.")
            return 0

        elif action == "rename":
            profile = service.rename_profile(args.old_name, args.new_name)
            if json_output:
                print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
            else:
                print(f"Profile '{args.old_name}' renamed to '{profile.name}'.")
            return 0

        elif action == "scan":
            scan_rec = service.scan_profile(args.name, label=args.label)
            if json_output:
                print(json.dumps(scan_rec.to_dict(), indent=2, sort_keys=True))
            else:
                summary = scan_rec.summary
                lines = [
                    f"=== Scan Snapshot for Profile: {args.name} ===",
                    f"Scan ID           : {summary.id}",
                    f"Scanned At        : {summary.scanned_at}",
                    f"Detected Loader   : {summary.loader or 'None'}",
                    f"Detected MC Ver   : {summary.mc_version or 'None'}",
                    f"Total Jars        : {summary.jar_count}",
                    f"Total Mods        : {summary.mod_count}",
                    f"Total Findings    : errors: {summary.error_count}, warnings: {summary.warning_count}, info: {summary.info_count}",
                ]
                if summary.label:
                    lines.append(f"Label             : {summary.label}")
                print("\n".join(lines))
            return 0

        elif action == "history":
            history = service.get_history(args.name, limit=args.limit)
            if json_output:
                print(json.dumps([s.to_dict() for s in history], indent=2, sort_keys=True))
            else:
                if not history:
                    print(f"No scans recorded for profile '{args.name}'.")
                else:
                    lines = [
                        f"{'ID':<6} {'SCANNED AT':<25} {'LOADER':<10} {'MC VER':<10} {'MODS':<6} {'ERRS':<6} {'PINNED':<8} LABEL",
                        "-" * 85,
                    ]
                    for s in history:
                        mc = s.mc_version or "-"
                        loader = s.loader or "-"
                        pinned_str = "YES" if s.pinned else "NO"
                        label_str = s.label or ""
                        lines.append(
                            f"{s.id:<6} {s.scanned_at:<25} {loader:<10} {mc:<10} {s.mod_count:<6} {s.error_count:<6} {pinned_str:<8} {label_str}"
                        )
                    print("\n".join(lines))
            return 0

        elif action == "diff":
            diff_file = getattr(args, "diff_file", None)
            if diff_file:
                snap_path = Path(diff_file).resolve()
                if not snap_path.is_file():
                    print(f"Error: Snapshot file '{snap_path}' does not exist.", file=sys.stderr)
                    return 2

                if len(args.scans) == 0:
                    profile = service.get_profile(args.name)
                    latest = service.repo.get_latest_scan_for_profile(profile.id)
                    if not latest:
                        print(f"Not found: Profile '{args.name}' has no scans to diff.", file=sys.stderr)
                        return 2
                    scan_id = latest.id
                elif len(args.scans) == 1:
                    scan_id = args.scans[0]
                else:
                    print("Usage error: 'profile diff --file' accepts at most 1 scan ID.", file=sys.stderr)
                    return 2

                stored_scan = service.get_scan(args.name, scan_id)
                snap_content = snap_path.read_text(encoding="utf-8")
                snap_scan = import_scan_json(snap_content)
                diff_res = diff_scans(stored_scan, snap_scan)
            else:
                if len(args.scans) == 0:
                    scan_a_id, scan_b_id = None, None
                elif len(args.scans) == 2:
                    scan_a_id, scan_b_id = args.scans[0], args.scans[1]
                else:
                    print("Usage error: 'profile diff' requires 0 scan IDs or exactly 2 scan IDs.", file=sys.stderr)
                    return 2

                diff_res = service.diff_scans(args.name, scan_a_id, scan_b_id)

            if json_output:
                print(json.dumps(diff_res.to_dict(), indent=2, sort_keys=True))
            else:
                if not diff_res.has_changes:
                    print(f"No differences found between scans for profile '{args.name}'.")
                else:
                    lines = [f"=== Diff for Profile: {args.name} ==="]
                    if diff_res.loader_changed:
                        lines.append(f"Loader Changed    : {diff_res.loader_changed['old']} -> {diff_res.loader_changed['new']}")
                    if diff_res.mc_version_changed:
                        lines.append(f"MC Version Changed: {diff_res.mc_version_changed['old']} -> {diff_res.mc_version_changed['new']}")

                    if diff_res.top_level_mods:
                        lines.append(f"Top-Level Mod Changes ({len(diff_res.top_level_mods)}):")
                        for m in diff_res.top_level_mods:
                            lines.append(f"  * [{m.change_type.upper()}] {m.id} (ver: {m.old_version} -> {m.new_version})")

                    if diff_res.nested_mods:
                        lines.append(f"Nested Mod Changes ({len(diff_res.nested_mods)}):")
                        for m in diff_res.nested_mods:
                            lines.append(f"  * [{m.change_type.upper()}] {m.id} (ver: {m.old_version} -> {m.new_version})")

                    if diff_res.findings_added:
                        lines.append(f"Findings Added ({len(diff_res.findings_added)}):")
                        for f in diff_res.findings_added:
                            lines.append(f"  * [{f.severity.upper()}] {f.code}: {f.message}")

                    if diff_res.findings_resolved:
                        lines.append(f"Findings Resolved ({len(diff_res.findings_resolved)}):")
                        for f in diff_res.findings_resolved:
                            lines.append(f"  * [{f.severity.upper()}] {f.code}: {f.message}")

                    print("\n".join(lines))
            return 0

        elif action == "export-scan":
            out_file = Path(args.out).resolve()
            if out_file.exists() and not getattr(args, "force", False):
                print(f"Error: Export target exists: Target file '{out_file}' already exists. Use --force to overwrite.", file=sys.stderr)
                return 1

            scan_res = service.get_scan(args.name, args.scan_id)
            json_text = export_scan_json(scan_res)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json_text, encoding="utf-8")
            if json_output:
                print(json.dumps({"exported_file": str(out_file), "scan_id": args.scan_id}, indent=2, sort_keys=True))
            else:
                print(f"Scan {args.scan_id} exported to '{out_file}'.")
            return 0

        elif action == "pin":
            service.set_scan_pinned(args.name, args.scan_id, pinned=True)
            if json_output:
                print(json.dumps({"pinned": True, "profile": args.name, "scan_id": args.scan_id}, indent=2, sort_keys=True))
            else:
                print(f"Pinned scan {args.scan_id} for profile '{args.name}'.")
            return 0

        elif action == "unpin":
            service.set_scan_pinned(args.name, args.scan_id, pinned=False)
            if json_output:
                print(json.dumps({"pinned": False, "profile": args.name, "scan_id": args.scan_id}, indent=2, sort_keys=True))
            else:
                print(f"Unpinned scan {args.scan_id} for profile '{args.name}'.")
            return 0

        print(f"Unknown action: {action}", file=sys.stderr)
        return 2

    except ProfileNotFoundError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 2
    except ScanNotFoundError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 2
    except SnapshotInvalidError as exc:
        print(f"Error: Snapshot invalid: {exc}", file=sys.stderr)
        return 1
    except ExportTargetExistsError as exc:
        print(f"Error: Export target exists: {exc}", file=sys.stderr)
        return 1
    except ProfilePathMissingError as exc:
        print(f"Path missing error: {exc}", file=sys.stderr)
        return 1
    except DuplicateProfileError as exc:
        print(f"Duplicate error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, StorageError, SchemaTooNewError) as exc:
        print(f"Operational error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def _handle_analyze(args: argparse.Namespace) -> int:
    target_dir = args.dir
    json_output = getattr(args, "json_output", False)
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        print(f"Error: Target path '{target_dir}' does not exist or is not a directory.", file=sys.stderr)
        return 2

    try:
        scan_res = scan(target_dir)
        report = analyze_pack(scan_res)
        rep_dict = report.to_dict()
        rep_dict["detected_loader"] = scan_res.detected_loader.value if scan_res.detected_loader else None
        rep_dict["detected_mc_version"] = scan_res.detected_mc_version
        rep_dict["scanned_path"] = scan_res.scanned_path

        if json_output:
            json_text = json.dumps(rep_dict, indent=2, sort_keys=True)
            print(json_text)
        else:
            lines = [
                "=== MMIS Modpack Analysis Report ===",
                f"Scanned Directory : {scan_res.scanned_path or '.'}",
                f"Detected Loader   : {scan_res.detected_loader.value if scan_res.detected_loader else 'None'}",
                f"Detected MC Ver   : {scan_res.detected_mc_version or 'None'}",
                f"Total Findings    : errors: {report.severity_counts.get('error', 0)}, warnings: {report.severity_counts.get('warning', 0)}, info: {report.severity_counts.get('info', 0)}",
            ]
            if report.findings:
                lines.append("\nFindings List:")
                for f in report.findings:
                    sev = f.severity.value.upper()
                    sugg = f" -> Suggestion: {f.suggestion}" if f.suggestion else ""
                    lines.append(f"  * [{sev}] {f.code}: {f.message}{sugg}")
            print("\n".join(lines))
        return 0
    except Exception as exc:
        print(f"Unexpected analysis error: {exc}", file=sys.stderr)
        return 1


def _handle_crash(args: argparse.Namespace) -> int:
    action = getattr(args, "crash_action", None)
    if not action:
        print("Usage error: missing crash subcommand (e.g. analyze, list, show, delete).", file=sys.stderr)
        return 2

    data_dir = getattr(args, "data_dir", None)
    json_output = getattr(args, "json_output", False)
    service = None

    def require_service():
        nonlocal service
        if service is None:
            service = _get_profile_service(data_dir)
        return service

    try:
        if action == "analyze":
            file_path = args.file
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                print(f"Error: Crash file '{file_path}' does not exist or is not a file.", file=sys.stderr)
                return 2

            scan_ctx = None
            if getattr(args, "pack", None):
                if not os.path.exists(args.pack) or not os.path.isdir(args.pack):
                    print(f"Error: Pack directory '{args.pack}' does not exist or is not a directory.", file=sys.stderr)
                    return 2
                scan_ctx = scan(args.pack)
            elif getattr(args, "profile", None):
                svc = require_service()
                prof = svc.get_profile(args.profile)
                latest_summary = svc.repo.get_latest_scan_for_profile(prof.id)
                if latest_summary:
                    scan_ctx = svc.get_scan(prof.name, latest_summary.id)

            raw_bytes = Path(file_path).read_bytes()
            analysis = analyze_crash(raw_bytes, scan=scan_ctx, source_name=Path(file_path).name)

            if getattr(args, "save", False):
                svc = require_service()
                prof_name = getattr(args, "profile", None)
                svc.import_crash(raw_bytes, profile_name=prof_name, source_name=Path(file_path).name)

            if json_output:
                print(json.dumps(analysis.to_dict(), indent=2, sort_keys=True))
            else:
                lines = [
                    "=== MMIS Crash Analysis ===",
                    f"Kind              : {analysis.kind.value}",
                    f"Description       : {analysis.description or 'None'}",
                    f"Detected Loader   : {analysis.detected_loader or 'None'}",
                    f"Detected MC Ver   : {analysis.mc_version or 'None'}",
                    f"Java Version      : {analysis.java_version or 'None'}",
                ]
                if analysis.root_cause:
                    lines.append(f"Root Cause        : {analysis.root_cause.exception_class}: {analysis.root_cause.message or ''}")
                if analysis.top_suspect:
                    lines.append(f"Top Suspect       : {analysis.top_suspect.name or analysis.top_suspect.mod_id} ({analysis.top_suspect.certainty.value}, rank {analysis.top_suspect.rank})")
                    lines.append(f"Suspect Reason    : {analysis.top_suspect.reason}")
                else:
                    lines.append("Top Suspect       : None attributable")

                if analysis.findings:
                    lines.append("\nFindings List:")
                    for f in analysis.findings:
                        sev = f.severity.value.upper()
                        sugg = f" -> Suggestion: {f.suggestion}" if f.suggestion else ""
                        lines.append(f"  * [{sev}] {f.code}: {f.message}{sugg}")
                print("\n".join(lines))
            return 0

        elif action == "list":
            svc = require_service()
            prof_name = getattr(args, "profile", None)
            crashes = svc.list_crashes(prof_name)
            if json_output:
                print(json.dumps([c.to_dict() for c in crashes], indent=2, sort_keys=True))
            else:
                if not crashes:
                    print("No crash reports found.")
                else:
                    lines = [
                        f"{'ID':<6} {'IMPORTED AT':<25} {'KIND':<15} {'MC VER':<10} {'TOP SUSPECT':<20} SOURCE",
                        "-" * 90,
                    ]
                    for c in crashes:
                        mc = c.mc_version or "-"
                        sus = c.top_suspect or "-"
                        lines.append(f"{c.id:<6} {c.imported_at:<25} {c.kind:<15} {mc:<10} {sus:<20} {c.source_name}")
                    print("\n".join(lines))
            return 0

        elif action == "show":
            svc = require_service()
            crash_id = args.id
            record, analysis = svc.get_crash(crash_id)
            if json_output:
                out = record.to_dict()
                out["analysis"] = analysis.to_dict()
                print(json.dumps(out, indent=2, sort_keys=True))
            else:
                lines = [
                    f"=== Crash Report ID: {record.id} ===",
                    f"Source Name       : {record.source_name}",
                    f"Imported At       : {record.imported_at}",
                    f"Kind              : {record.kind}",
                    f"Profile ID        : {record.profile_id or 'None'}",
                    f"Top Suspect       : {record.top_suspect or 'None'}",
                ]
                if analysis.root_cause:
                    lines.append(f"Root Cause        : {analysis.root_cause.exception_class}: {analysis.root_cause.message or ''}")
                if analysis.findings:
                    lines.append("\nFindings List:")
                    for f in analysis.findings:
                        sev = f.severity.value.upper()
                        lines.append(f"  * [{sev}] {f.code}: {f.message}")
                    print("\n".join(lines))
            return 0

        elif action == "delete":
            svc = require_service()
            crash_id = args.id
            deleted = svc.delete_crash(crash_id)
            if json_output:
                print(json.dumps({"deleted": deleted, "id": crash_id}, indent=2, sort_keys=True))
            else:
                if deleted:
                    print(f"Crash report {crash_id} deleted.")
                else:
                    print(f"Crash report {crash_id} not found.", file=sys.stderr)
                    return 2
            return 0

        print(f"Unknown crash action: {action}", file=sys.stderr)
        return 2

    except ProfileNotFoundError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Not found error: {exc}", file=sys.stderr)
        return 2
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def _handle_report(args: argparse.Namespace) -> int:
    data_dir = getattr(args, "data_dir", None)
    out_path = Path(args.out).resolve()
    if out_path.exists() and not getattr(args, "force", False):
        print(f"Error: Export target exists: File '{out_path}' already exists. Use --force to overwrite.", file=sys.stderr)
        return 1

    try:
        service = _get_profile_service(data_dir)
        profile = service.get_profile(args.profile)
        if args.scan is not None:
            scan_rec = service.get_scan_record(args.profile, args.scan)
        else:
            scan_rec = service.get_latest_scan_record(args.profile)
            if scan_rec is None:
                print(f"Not found: Profile '{args.profile}' has no scans to report on.", file=sys.stderr)
                return 2

        latest_crash = service.get_latest_crash(args.profile)
        report = build_diagnostic_report(
            profile_name=profile.name,
            scan_record=scan_rec,
            profile_path=profile.path,
            latest_crash=latest_crash,
        )

        if args.format == "json":
            content = export_report_json(report)
        else:
            content = export_report_html(report)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"Report saved to {out_path}")
        return 0

    except ProfileNotFoundError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 2
    except ScanNotFoundError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 2
    except ExportTargetExistsError as exc:
        print(f"Error: Export target exists: {exc}", file=sys.stderr)
        return 1
    except (StorageError, SchemaTooNewError) as exc:
        print(f"Operational error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def _handle_backup(args: argparse.Namespace) -> int:
    action = getattr(args, "backup_action", None)
    if not action:
        print("Usage error: missing backup subcommand (create, verify, restore).", file=sys.stderr)
        return 2

    data_dir = getattr(args, "data_dir", None)
    json_output = getattr(args, "json_output", False)

    try:
        if action == "create":
            out_path = Path(args.out).resolve()
            if out_path.exists() and not getattr(args, "force", False):
                print(f"Error: Export target exists: Target backup file '{out_path}' already exists. Use --force to overwrite.", file=sys.stderr)
                return 1

            saved_path = create_backup(
                data_dir=data_dir,
                out_path=out_path,
                force=getattr(args, "force", False),
            )
            if json_output:
                print(json.dumps({"backup_path": str(saved_path), "success": True}, indent=2, sort_keys=True))
            else:
                print(f"Backup created successfully: {saved_path}")
            return 0

        elif action == "verify":
            zip_file = Path(args.file).resolve()
            manifest = verify_backup(zip_file)
            if json_output:
                print(json.dumps(manifest, indent=2, sort_keys=True))
            else:
                files_count = len(manifest.get("files", {}))
                print(f"Backup archive is valid (schema v{manifest.get('schema_version')}, {files_count} files, created {manifest.get('created_at')}).")
            return 0

        elif action == "restore":
            zip_file = Path(args.file).resolve()
            if not getattr(args, "yes", False):
                verify_backup(zip_file)
                print(
                    f"Notice: Restoring backup from '{zip_file}' will replace the current MMIS database and settings.\n"
                    f"A safety backup of the current database will be automatically created.\n"
                    f"To confirm restoration, re-run with --yes.",
                    file=sys.stderr,
                )
                return 1

            safety_backup = restore_backup(
                zip_path=zip_file,
                data_dir=data_dir,
                force=getattr(args, "force", False),
            )
            if json_output:
                print(json.dumps({"restored_from": str(zip_file), "safety_backup": str(safety_backup), "success": True}, indent=2, sort_keys=True))
            else:
                print(f"Restore completed successfully. Safety backup saved to: {safety_backup}")
            return 0

        print(f"Unknown backup action: {action}", file=sys.stderr)
        return 2

    except BackupInvalidError as exc:
        print(f"Error: Backup invalid: {exc.reason}", file=sys.stderr)
        return 1
    except ExportTargetExistsError as exc:
        print(f"Error: Export target exists: {exc}", file=sys.stderr)
        return 1
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint function. Returns exit code (0 on success, 2 on usage/not found, 1 on operational error)."""
    parser = _build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    except argparse.ArgumentError as exc:
        print(f"Usage error: {exc}", file=sys.stderr)
        return 2

    if args.command == "scan":
        return _handle_scan(args)
    elif args.command == "profile":
        return _handle_profile(args)
    elif args.command == "analyze":
        return _handle_analyze(args)
    elif args.command == "crash":
        return _handle_crash(args)
    elif args.command == "report":
        return _handle_report(args)
    elif args.command == "backup":
        return _handle_backup(args)
    elif args.command == "gui":
        from mmis.ui.app import launch_gui
        data_dir = getattr(args, "data_dir", None)
        return launch_gui(data_dir=data_dir)
    else:
        parser.print_usage(file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
