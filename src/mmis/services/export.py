"""Export services for MMIS diagnostic reports and scan snapshots.

Supports deterministic JSON export/import and secure standalone HTML report generation.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from mmis.domain.errors import SnapshotInvalidError
from mmis.domain.models import ScanResult
from mmis.services.report import DiagnosticReport, ReportFinding

DEFAULT_PALETTE: dict[str, str] = {
    "bg_window": "#0F1115",
    "surface_1": "#161A20",
    "surface_2": "#1E232B",
    "surface_3": "#262C36",
    "border_hairline": "#2D333B",
    "text_primary": "#E6EDF3",
    "text_secondary": "#9DA7B3",
    "text_muted": "#8B949E",
    "accent": "#3FB950",
    "color_error": "#F85149",
    "color_error_bg": "#381B1B",
    "color_warning": "#D29922",
    "color_warning_bg": "#332615",
    "color_info": "#58A6FF",
    "color_info_bg": "#162842",
    "color_success": "#3FB950",
    "color_success_bg": "#132D1B",
}


def _esc(value: Any) -> str:
    """HTML escape untrusted strings, integers, or None values."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def export_report_json(report: DiagnosticReport) -> str:
    """Export a DiagnosticReport as deterministic formatted JSON."""
    data = report.to_dict()
    return json.dumps(data, indent=2, sort_keys=True)


def export_scan_json(scan: ScanResult) -> str:
    """Export a ScanResult snapshot as deterministic JSON with format and schema_version."""
    payload = {
        "format": "mmis_scan_snapshot",
        "scan": scan.to_dict(),
        "schema_version": 1,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def import_scan_json(json_str: str) -> ScanResult:
    """Parse and validate an exported ScanResult JSON snapshot.

    Raises:
        SnapshotInvalidError: If JSON is invalid, corrupted, or missing required scan structure.
    """
    try:
        data = json.loads(json_str)
    except Exception as exc:
        raise SnapshotInvalidError(f"Invalid JSON in snapshot: {exc}") from exc

    if not isinstance(data, dict):
        raise SnapshotInvalidError("Snapshot root must be a JSON object.")

    if data.get("format") != "mmis_scan_snapshot":
        raise SnapshotInvalidError("Invalid snapshot: missing or invalid 'format' marker.")

    schema_version = data.get("schema_version")
    if schema_version != 1:
        raise SnapshotInvalidError(f"Unsupported snapshot schema_version: {schema_version}")

    if "scan" not in data or not isinstance(data["scan"], dict):
        raise SnapshotInvalidError("Missing 'scan' dictionary payload in snapshot.")

    scan_data = data["scan"]

    # Validate required top-level keys inside scan
    required_keys = {"scanned_path", "mods_dir", "jars", "mods", "findings"}
    if not required_keys.issubset(scan_data.keys()):
        missing = sorted(required_keys - scan_data.keys())
        raise SnapshotInvalidError(f"Snapshot scan data missing required keys: {missing}")

    if not isinstance(scan_data.get("scanned_path"), str) or not isinstance(scan_data.get("mods_dir"), str):
        raise SnapshotInvalidError("Snapshot scanned_path and mods_dir must be strings.")

    if not isinstance(scan_data.get("jars"), list):
        raise SnapshotInvalidError("Snapshot 'jars' must be a list.")

    if not isinstance(scan_data.get("mods"), list):
        raise SnapshotInvalidError("Snapshot 'mods' must be a list.")

    if not isinstance(scan_data.get("findings"), list):
        raise SnapshotInvalidError("Snapshot 'findings' must be a list.")

    # Validate jars
    for j in scan_data["jars"]:
        if not isinstance(j, dict) or "path" not in j or "size_bytes" not in j or "sha256" not in j:
            raise SnapshotInvalidError("Snapshot contains invalid JarFile entry.")
        if not isinstance(j["size_bytes"], int) or not isinstance(j["sha256"], str):
            raise SnapshotInvalidError("Snapshot JarFile entry has invalid field types.")

    # Validate mods
    for m in scan_data["mods"]:
        if not isinstance(m, dict) or "id" not in m or not isinstance(m["id"], str):
            raise SnapshotInvalidError("Snapshot contains invalid ModInfo entry.")
        if "loaders" in m and not isinstance(m["loaders"], list):
            raise SnapshotInvalidError("Snapshot ModInfo entry has invalid 'loaders' field type.")

    # Validate findings
    for f in scan_data["findings"]:
        if not isinstance(f, dict) or "code" not in f or "severity" not in f or "certainty" not in f or "message" not in f:
            raise SnapshotInvalidError("Snapshot contains invalid Finding entry.")
        if "evidence" not in f or not isinstance(f["evidence"], list):
            raise SnapshotInvalidError("Snapshot Finding entry missing 'evidence' list.")

    try:
        return ScanResult.from_dict(scan_data)
    except Exception as exc:
        raise SnapshotInvalidError(f"Corrupt ScanResult data structure: {exc}") from exc


def _render_findings_table(findings: list[ReportFinding]) -> str:
    if not findings:
        return "<p class=\"text-muted\">No issues detected in this category.</p>"

    rows: list[str] = []
    for f in findings:
        sev_class = "badge-error" if f.severity == "error" else ("badge-warning" if f.severity == "warning" else "badge-info")
        mods_str = ", ".join(_esc(m) for m in f.related_mod_ids) if f.related_mod_ids else "-"
        sug_html = f"<div class=\"suggestion\"><strong>Recommendation:</strong> {_esc(f.suggestion)}</div>" if f.suggestion else ""

        evidence_html = ""
        if f.evidence:
            ev_rows = "".join(
                f"<tr><td><code>{_esc(e.file)}</code></td><td>{_esc(e.field or '-')}</td><td><code>{_esc(e.raw_value or '-')}</code></td><td>{_esc(e.message or '-')}</td></tr>"
                for e in f.evidence
            )
            evidence_html = (
                f"<div class=\"evidence-table-wrap\">"
                f"<table class=\"evidence-table\">"
                f"<thead><tr><th>File</th><th>Field</th><th>Raw Value</th><th>Detail</th></tr></thead>"
                f"<tbody>{ev_rows}</tbody>"
                f"</table></div>"
            )

        rows.append(
            f"<div class=\"finding-card\">"
            f"<div class=\"finding-header\">"
            f"<span class=\"badge {sev_class}\">{_esc(f.severity.upper())}</span> "
            f"<span class=\"badge badge-certainty\">{_esc(f.certainty)}</span> "
            f"<strong><code>{_esc(f.code)}</code></strong>: {_esc(f.message)}"
            f"</div>"
            f"<div class=\"finding-mods\"><strong>Affected mods:</strong> {mods_str}</div>"
            f"{sug_html}"
            f"{evidence_html}"
            f"</div>"
        )

    return "\n".join(rows)


def export_report_html(report: DiagnosticReport, palette: dict[str, str] | None = None) -> str:
    """Generate a single-file, self-contained HTML diagnostic report.

    Features:
    - Secure: Strict CSP, no scripts, no remote URLs, every interpolated value escaped.
    - Inline dark theme stylesheet aligned with app tokens.
    - Print-ready stylesheet with light background.
    - Comprehensive sections: Summary, Recommendations, Findings by category,
      Dependency problems, Conflicts by certainty, Crash analysis, Mod inventory.
    """
    pal = dict(DEFAULT_PALETTE)
    if palette:
        pal.update(palette)

    # Status badge class
    status_class = "badge-success" if report.status == "Healthy" else ("badge-warning" if report.status == "Warnings" else "badge-error")

    # Recommendations HTML
    rec_cards: list[str] = []
    if report.recommendations:
        for idx, r in enumerate(report.recommendations, 1):
            sev_class = "badge-error" if r.severity == "error" else ("badge-warning" if r.severity == "warning" else "badge-info")
            codes = ", ".join(f"<code>{_esc(c)}</code>" for c in r.finding_codes)
            mods = ", ".join(_esc(m) for m in r.affected_mod_ids) if r.affected_mod_ids else "None"
            rec_cards.append(
                f"<div class=\"recommendation-item\">"
                f"<div class=\"rec-header\"><span class=\"badge {sev_class}\">{_esc(r.severity.upper())}</span> "
                f"<strong>#{idx}: {_esc(r.suggestion)}</strong></div>"
                f"<div class=\"rec-meta\">Affected mods: {mods} | Findings: {codes}</div>"
                f"</div>"
            )
        recs_html = "\n".join(rec_cards)
    else:
        recs_html = "<p class=\"text-muted\">No recommendations. The modpack appears clean and well-configured.</p>"

    # Findings by category
    metadata_findings = report.findings_by_category.get("metadata", [])
    dependency_findings = report.findings_by_category.get("dependency", [])
    conflict_findings = report.findings_by_category.get("conflict", [])

    meta_html = _render_findings_table(metadata_findings)
    dep_html = _render_findings_table(dependency_findings)
    conf_html = _render_findings_table(conflict_findings)

    # Conflicts by certainty class
    conflicts_by_cert_sections: list[str] = []
    for cert_label in ["Certain", "Possible", "Insufficient data"]:
        c_list = report.conflicts_by_certainty.get(cert_label, [])
        badge_style = "badge-error" if cert_label == "Certain" else ("badge-warning" if cert_label == "Possible" else "badge-info")
        conflicts_by_cert_sections.append(
            f"<h4><span class=\"badge {badge_style}\">{_esc(cert_label)}</span> ({len(c_list)})</h4>"
            f"{_render_findings_table(c_list)}"
        )
    conflicts_cert_html = "\n".join(conflicts_by_cert_sections)

    # Crash analysis section
    if report.latest_crash:
        cr = report.latest_crash
        suspects_rows = "".join(
            f"<tr><td><strong>{_esc(s.get('mod_name') or s.get('mod_id'))}</strong> (<code>{_esc(s.get('mod_id'))}</code>)</td>"
            f"<td><span class=\"badge badge-certainty\">{_esc(s.get('certainty'))}</span> ({_esc(s.get('confidence_score'))}%)</td>"
            f"<td>{_esc(s.get('reason'))}</td>"
            f"<td><code>{_esc(s.get('jar_file') or '-')}</code></td></tr>"
            for s in cr.suspects
        )
        suspects_table = (
            f"<table class=\"table\">"
            f"<thead><tr><th>Suspect Mod</th><th>Confidence</th><th>Reason</th><th>Jar File</th></tr></thead>"
            f"<tbody>{suspects_rows}</tbody>"
            f"</table>"
            if suspects_rows
            else "<p class=\"text-muted\">No specific mod suspects identified.</p>"
        )

        crash_findings_html = _render_findings_table(cr.findings)

        crash_html = f"""
        <div class="card">
            <h3>Latest Crash Analysis (Report #{_esc(cr.crash_id)})</h3>
            <div class="meta-grid">
                <div><strong>Source:</strong> <code>{_esc(cr.source_name)}</code></div>
                <div><strong>Imported At:</strong> {_esc(cr.imported_at)}</div>
                <div><strong>Kind:</strong> {_esc(cr.kind)}</div>
                <div><strong>Top Suspect:</strong> <code>{_esc(cr.top_suspect or 'None')}</code></div>
            </div>
            <div class="root-cause-box">
                <strong>Root Cause:</strong>
                <div><code>{_esc(cr.root_cause or 'No root cause identified')}</code></div>
            </div>
            <h4>Ranked Suspects</h4>
            {suspects_table}
            <h4>Crash Findings</h4>
            {crash_findings_html}
        </div>
        """
    else:
        crash_html = "<p class=\"text-muted\">No crash report associated with this profile scan.</p>"

    # Mod Inventory Table
    mod_rows: list[str] = []
    for m in report.mod_inventory.mods:
        status_tag = "<span class=\"badge badge-disabled\">Disabled</span>" if m.is_disabled else "<span class=\"badge badge-success\">Active</span>"
        desc_attr = f' title="{_esc(m.description)}"' if m.description else ""
        mod_rows.append(
            f"<tr{desc_attr}>"
            f"<td>{status_tag}</td>"
            f"<td><strong>{_esc(m.name)}</strong></td>"
            f"<td><code>{_esc(m.mod_id)}</code></td>"
            f"<td>{_esc(m.version)}</td>"
            f"<td>{_esc(m.loader)}</td>"
            f"<td><code>{_esc(m.jar_name)}</code></td>"
            f"</tr>"
        )
    mod_table_html = "\n".join(mod_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <title>MMIS Diagnostic Report - {_esc(report.profile_name)}</title>
    <style>
        :root {{
            --bg-window: {pal["bg_window"]};
            --surface-1: {pal["surface_1"]};
            --surface-2: {pal["surface_2"]};
            --surface-3: {pal["surface_3"]};
            --border-hairline: {pal["border_hairline"]};
            --text-primary: {pal["text_primary"]};
            --text-secondary: {pal["text_secondary"]};
            --text-muted: {pal["text_muted"]};
            --accent: {pal["accent"]};
            --color-error: {pal["color_error"]};
            --color-error-bg: {pal["color_error_bg"]};
            --color-warning: {pal["color_warning"]};
            --color-warning-bg: {pal["color_warning_bg"]};
            --color-info: {pal["color_info"]};
            --color-info-bg: {pal["color_info_bg"]};
            --color-success: {pal["color_success"]};
            --color-success-bg: {pal["color_success_bg"]};
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-window);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 14px;
            line-height: 1.5;
            padding: 32px 24px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1, h2, h3, h4 {{ color: var(--text-primary); font-weight: 600; margin-bottom: 12px; }}
        h1 {{ font-size: 26px; border-bottom: 2px solid var(--border-hairline); padding-bottom: 12px; margin-bottom: 24px; }}
        h2 {{ font-size: 20px; margin-top: 32px; border-bottom: 1px solid var(--border-hairline); padding-bottom: 8px; }}
        h3 {{ font-size: 16px; margin-top: 16px; }}
        h4 {{ font-size: 14px; margin-top: 14px; margin-bottom: 8px; }}
        p {{ margin-bottom: 12px; }}
        code {{
            background: var(--surface-2);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
            font-size: 13px;
        }}
        .card {{
            background: var(--surface-1);
            border: 1px solid var(--border-hairline);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }}
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-top: 16px;
        }}
        .stat-box {{
            background: var(--surface-2);
            padding: 12px;
            border-radius: 6px;
            border: 1px solid var(--border-hairline);
            text-align: center;
        }}
        .stat-value {{ font-size: 22px; font-weight: bold; margin-top: 4px; }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-error {{ background: var(--color-error-bg); color: var(--color-error); border: 1px solid var(--color-error); }}
        .badge-warning {{ background: var(--color-warning-bg); color: var(--color-warning); border: 1px solid var(--color-warning); }}
        .badge-info {{ background: var(--color-info-bg); color: var(--color-info); border: 1px solid var(--color-info); }}
        .badge-success {{ background: var(--color-success-bg); color: var(--color-success); border: 1px solid var(--color-success); }}
        .badge-disabled {{ background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border-hairline); }}
        .badge-certainty {{ background: var(--surface-2); color: var(--text-secondary); border: 1px solid var(--border-hairline); font-size: 11px; }}
        .recommendation-item {{
            background: var(--surface-2);
            border-left: 4px solid var(--accent);
            padding: 12px 16px;
            border-radius: 4px;
            margin-bottom: 12px;
        }}
        .rec-header {{ font-size: 15px; margin-bottom: 6px; }}
        .rec-meta {{ font-size: 12px; color: var(--text-secondary); }}
        .finding-card {{
            background: var(--surface-2);
            border: 1px solid var(--border-hairline);
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 12px;
        }}
        .finding-header {{ font-size: 14px; margin-bottom: 6px; }}
        .finding-mods {{ font-size: 13px; color: var(--text-secondary); margin-bottom: 8px; }}
        .suggestion {{
            background: var(--surface-3);
            padding: 8px 12px;
            border-radius: 4px;
            margin: 8px 0;
            font-size: 13px;
        }}
        .evidence-table-wrap {{ margin-top: 8px; overflow-x: auto; }}
        .evidence-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        .evidence-table th, .evidence-table td {{
            border: 1px solid var(--border-hairline);
            padding: 6px 10px;
            text-align: left;
        }}
        .evidence-table th {{ background: var(--surface-3); color: var(--text-secondary); }}
        .table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }}
        .table th, .table td {{
            border: 1px solid var(--border-hairline);
            padding: 8px 12px;
            text-align: left;
        }}
        .table th {{ background: var(--surface-2); color: var(--text-secondary); }}
        .table tr:hover {{ background: var(--surface-2); }}
        .root-cause-box {{
            background: var(--surface-2);
            border-left: 4px solid var(--color-error);
            padding: 10px 14px;
            border-radius: 4px;
            margin: 12px 0;
        }}
        .text-muted {{ color: var(--text-muted); font-style: italic; }}
        @media print {{
            body {{
                background-color: #FFFFFF !important;
                color: #111111 !important;
                padding: 0;
            }}
            .card, .finding-card, .recommendation-item, .stat-box {{
                background: #FFFFFF !important;
                color: #111111 !important;
                border: 1px solid #CCCCCC !important;
            }}
            code {{ background: #EEEEEE !important; color: #111111 !important; }}
            .table th, .evidence-table th {{ background: #EEEEEE !important; color: #111111 !important; }}
            .table td, .evidence-table td {{ border: 1px solid #DDDDDD !important; }}
            .badge-error {{ color: #CC0000 !important; border-color: #CC0000 !important; background: transparent !important; }}
            .badge-warning {{ color: #996600 !important; border-color: #996600 !important; background: transparent !important; }}
            .badge-info {{ color: #0066CC !important; border-color: #0066CC !important; background: transparent !important; }}
            .badge-success {{ color: #008800 !important; border-color: #008800 !important; background: transparent !important; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MMIS Diagnostic Report: {_esc(report.profile_name)}</h1>

        <!-- Section 1: Summary -->
        <div class="card">
            <h2>Pack Identity & Health Summary</h2>
            <div class="meta-grid">
                <div><strong>Profile Name:</strong> {_esc(report.profile_name)}</div>
                <div><strong>Profile Directory:</strong> <code>{_esc(report.profile_path)}</code></div>
                <div><strong>Detected Loader:</strong> {_esc(report.detected_loader or 'None')}</div>
                <div><strong>Detected MC Version:</strong> {_esc(report.detected_mc_version or 'None')}</div>
                <div><strong>Scan ID:</strong> {_esc(report.scan_id)}</div>
                <div><strong>Scan Timestamp:</strong> {_esc(report.scanned_at)}</div>
                <div><strong>Scan Label:</strong> {_esc(report.scan_label or 'None')}</div>
                <div><strong>Pack Status:</strong> <span class="badge {status_class}">{_esc(report.status)}</span></div>
            </div>

            <div class="stat-grid">
                <div class="stat-box">
                    <div>Errors</div>
                    <div class="stat-value" style="color: var(--color-error);">{_esc(report.severity_counts.get('error', 0))}</div>
                </div>
                <div class="stat-box">
                    <div>Warnings</div>
                    <div class="stat-value" style="color: var(--color-warning);">{_esc(report.severity_counts.get('warning', 0))}</div>
                </div>
                <div class="stat-box">
                    <div>Info Findings</div>
                    <div class="stat-value" style="color: var(--color-info);">{_esc(report.severity_counts.get('info', 0))}</div>
                </div>
                <div class="stat-box">
                    <div>Active Mods</div>
                    <div class="stat-value" style="color: var(--accent);">{_esc(report.mod_inventory.enabled_mods)} / {_esc(report.mod_inventory.total_mods)}</div>
                </div>
            </div>

            <h3 style="margin-top: 20px;">Certainty Breakdown</h3>
            <div class="stat-grid">
                <div class="stat-box">
                    <div>Certain</div>
                    <div class="stat-value">{_esc(report.certainty_counts.get('Certain', 0))}</div>
                </div>
                <div class="stat-box">
                    <div>Possible</div>
                    <div class="stat-value">{_esc(report.certainty_counts.get('Possible', 0))}</div>
                </div>
                <div class="stat-box">
                    <div>Insufficient Data</div>
                    <div class="stat-value">{_esc(report.certainty_counts.get('Insufficient data', 0))}</div>
                </div>
            </div>
        </div>

        <!-- Section 2: Recommendations -->
        <h2>Actionable Recommendations</h2>
        {recs_html}

        <!-- Section 3: Findings by Category -->
        <h2>Findings by Category</h2>
        <h3>Metadata Findings ({len(metadata_findings)})</h3>
        {meta_html}

        <h3>Dependency Problems ({len(dependency_findings)})</h3>
        {dep_html}

        <h3>Conflict Findings ({len(conflict_findings)})</h3>
        {conf_html}

        <!-- Section 4: Conflicts by Certainty -->
        <h2>Conflicts Classified by Certainty</h2>
        {conflicts_cert_html}

        <!-- Section 5: Crash Analysis -->
        <h2>Crash & Log Analysis</h2>
        {crash_html}

        <!-- Section 6: Mod Inventory -->
        <h2>Mod Inventory ({_esc(report.mod_inventory.total_mods)} mods in {_esc(report.mod_inventory.total_jars)} jars)</h2>
        <table class="table">
            <thead>
                <tr>
                    <th>Status</th>
                    <th>Name</th>
                    <th>Mod ID</th>
                    <th>Version</th>
                    <th>Loader</th>
                    <th>Jar File</th>
                </tr>
            </thead>
            <tbody>
                {mod_table_html}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
