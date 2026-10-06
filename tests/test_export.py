"""Unit and security tests for export services (JSON and HTML)."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from mmis.domain.errors import SnapshotInvalidError
from mmis.domain.models import Certainty, Evidence, Finding, JarFile, Loader, ModInfo, ScanResult, Severity
from mmis.analysis.engine import AnalysisReport
from mmis.domain.profiles import ScanRecord, ScanSummary
from mmis.services.export import (
    export_report_html,
    export_report_json,
    export_scan_json,
    import_scan_json,
)
from mmis.services.report import build_diagnostic_report


class WellFormedParser(HTMLParser):
    """Verifies that the generated HTML can be fully parsed without syntax errors."""

    def __init__(self):
        super().__init__()
        self.tag_stack: list[str] = []
        self.self_closing = {"meta", "link", "img", "br", "hr", "input"}

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in self.self_closing:
            self.tag_stack.append(tag.lower())

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower not in self.self_closing and self.tag_stack:
            if self.tag_stack[-1] == tag_lower:
                self.tag_stack.pop()


def test_scan_snapshot_round_trip():
    scan = ScanResult(
        scanned_path="C:/games/minecraft/test_pack",
        mods_dir="C:/games/minecraft/test_pack/mods",
        jars=[JarFile(path="mods/test.jar", size_bytes=1024, sha256="deadbeef")],
        mods=[
            ModInfo(
                id="test_mod",
                name="Test Mod",
                version="1.2.3",
                loaders=[Loader.FABRIC],
                jar_path="mods/test.jar",
            )
        ],
        findings=[
            Finding(
                code="NO_METADATA",
                severity=Severity.WARNING,
                certainty=Certainty.POSSIBLE,
                message="Missing metadata",
                evidence=[Evidence(file="mods/test.jar", message="Missing metadata")],
                related_mod_ids=["test_mod"],
            )
        ],
        detected_loader=Loader.FABRIC,
        detected_mc_version="1.20.1",
    )

    exported_json = export_scan_json(scan)
    assert "schema_version" in exported_json

    imported_scan = import_scan_json(exported_json)
    assert imported_scan.scanned_path == scan.scanned_path
    assert imported_scan.detected_loader == scan.detected_loader
    assert imported_scan.detected_mc_version == scan.detected_mc_version
    assert len(imported_scan.mods) == len(scan.mods)
    assert imported_scan.mods[0].id == scan.mods[0].id
    assert imported_scan.findings[0].code == scan.findings[0].code


def test_import_scan_invalid_raises_snapshot_error():
    # 1. Truncated or malformed JSON
    with pytest.raises(SnapshotInvalidError):
        import_scan_json('{"format": "mmis_scan_snapshot", "schema_version": 1, "scan":')

    # 2. Non-dict root
    with pytest.raises(SnapshotInvalidError):
        import_scan_json("[]")

    # 3. Missing format marker (e.g. {"nope": 1})
    with pytest.raises(SnapshotInvalidError):
        import_scan_json('{"nope": 1}')

    # 4. Report JSON passed as snapshot
    report_json = '{"schema_version": 1, "profile_name": "Test", "status": "Healthy", "severity_counts": {}}'
    with pytest.raises(SnapshotInvalidError):
        import_scan_json(report_json)

    # 5. Unsupported schema_version
    with pytest.raises(SnapshotInvalidError):
        import_scan_json('{"format": "mmis_scan_snapshot", "schema_version": 999, "scan": {}}')

    # 6. Wrong keys inside scan
    with pytest.raises(SnapshotInvalidError):
        import_scan_json('{"format": "mmis_scan_snapshot", "schema_version": 1, "scan": {"scanned_path": "x"}}')

    # 7. Wrong types inside scan (e.g. jars is not a list)
    with pytest.raises(SnapshotInvalidError):
        import_scan_json(
            '{"format": "mmis_scan_snapshot", "schema_version": 1, "scan": {'
            '"scanned_path": "x", "mods_dir": "m", "jars": "not_a_list", "mods": [], "findings": []}}'
        )


def test_html_export_security_hostile_metadata_and_csp(tmp_path: Path):
    hostile_mod = ModInfo(
        id='"><img src=x onerror=alert(1)>',
        name="<script>alert(1)</script>",
        version="1.0.0",
        loaders=[Loader.FABRIC],
        jar_path=str(tmp_path / "mods" / "hostile.jar"),
        description="</style><script>bad()</script>",
    )
    scan_res = ScanResult(
        scanned_path=str(tmp_path),
        mods_dir=str(tmp_path / "mods"),
        jars=[JarFile(path=str(tmp_path / "mods" / "hostile.jar"), size_bytes=500, sha256="abc")],
        mods=[hostile_mod],
        findings=[
            Finding(
                code="CORRUPT_JAR",
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message="<b onmouseover=alert(1)>bad message</b>",
                evidence=[Evidence(file="mods/hostile.jar", message="Hostile payload")],
                related_mod_ids=[hostile_mod.id],
                suggestion="<script>alert('fix')</script>",
            )
        ],
        detected_loader=Loader.FABRIC,
        detected_mc_version="1.20.1",
    )
    summary = ScanSummary(
        id=1,
        profile_id=1,
        scanned_at="2026-10-05T12:00:00+00:00",
        loader="fabric",
        mc_version="1.20.1",
        jar_count=1,
        mod_count=1,
        error_count=1,
        warning_count=0,
        info_count=0,
        label="Hostile Scan",
    )
    analysis_rep = AnalysisReport()
    scan_record = ScanRecord(summary=summary, result=scan_res, analysis=analysis_rep)

    report = build_diagnostic_report("Hostile Profile", scan_record, profile_path=str(tmp_path))
    html_content = export_report_html(report)

    # 1. HTML well-formed parse check
    parser = WellFormedParser()
    parser.feed(html_content)
    # Tag stack should be cleanly closed or empty
    assert len(parser.tag_stack) == 0

    # 2. CSP Meta tag present
    assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">' in html_content

    # 3. Hostile injections must NOT exist unescaped
    assert "<script>" not in html_content
    assert "</script>" not in html_content
    assert "alert(1)" not in html_content.replace("&lt;", "<").replace("&quot;", '"') or (
        "&lt;script&gt;alert(1)&lt;/script&gt;" in html_content
    )
    assert '<img src=x onerror=alert(1)>' not in html_content
    assert "</style><script>bad()</script>" not in html_content
    assert "&lt;/style&gt;&lt;script&gt;bad()&lt;/script&gt;" in html_content
    assert html_content.count("</style>") == 1  # Only the legitimate stylesheet closing tag

    # 4. Strictly no external URLs or <script tags anywhere in the file
    lower_html = html_content.lower()
    assert "<script" not in lower_html
    assert "http://" not in lower_html
    assert "https://" not in lower_html
