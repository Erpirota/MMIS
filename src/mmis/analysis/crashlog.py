"""Crash report and log analyzer for MMIS.

Linear-time line-by-line parsing of Minecraft crash reports, Log4j logs,
JVM crash reports (hs_err), and explicit mod loader diagnostics.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mmis.domain.crash import (
    CrashAnalysisResult,
    CrashReportKind,
    CrashSuspect,
    ExceptionInfo,
    ModEntry,
    StackFrame,
)
from mmis.domain.findings import (
    CRASH_CLASS_NOT_FOUND,
    CRASH_GENERIC_EXCEPTION,
    CRASH_JAVA_VERSION_MISMATCH,
    CRASH_LOADER_EXPLICIT_ISSUE,
    CRASH_MIXIN_FAILURE,
    CRASH_NATIVE_JVM,
    CRASH_NO_EXCEPTION_FOUND,
    CRASH_NO_SUCH_METHOD_FIELD,
    CRASH_OOM,
    CRASH_STACK_OVERFLOW,
    CRASH_TRUNCATED,
)
from mmis.domain.models import Certainty, Evidence, Finding, ScanResult, Severity

MAX_LOG_SIZE = 20 * 1024 * 1024  # 20 MB
MAX_EVIDENCE_LINE_CHARS = 300
MAX_EVIDENCE_LINES_TOTAL = 200

PLATFORM_IDS = frozenset(
    {
        "minecraft",
        "java",
        "fabricloader",
        "fabric-loader",
        "quilt_loader",
        "forge",
        "neoforge",
        "fml",
        "javafml",
    }
)

PLATFORM_JAR_PREFIXES = (
    "minecraft",
    "client-",
    "server-",
    "forge-",
    "neoforge-",
    "fml-",
    "modlauncher-",
    "fabric-loader-",
    "fabric_loader-",
    "quilt-loader-",
    "quilt_loader-",
    "sponge-mixin",
    "mixin-",
    "mixinextras",
    "lwjgl",
    "netty",
    "asm-",
    "guava",
    "gson",
    "log4j",
    "slf4j",
    "commons-",
    "fastutil",
)

PLATFORM_PACKAGES = (
    "java.",
    "javax.",
    "sun.",
    "jdk.",
    "net.minecraft.",
    "com.mojang.",
    "net.minecraftforge.",
    "net.neoforged.",
    "cpw.mods.fml.",
    "cpw.mods.modlauncher.",
    "net.fabricmc.loader.",
    "org.quiltmc.loader.",
    "org.spongepowered.asm.",
    "com.llamalad7.mixinextras.",
    "org.lwjgl.",
    "io.netty.",
    "org.objectweb.asm.",
    "com.google.",
    "org.apache.",
    "org.slf4j.",
    "it.unimi.dsi.fastutil.",
)

LOADER_WRAPPER_EXCEPTIONS = frozenset(
    {
        "net.fabricmc.loader.impl.FormattedException",
        "net.fabricmc.loader.impl.discovery.ModResolutionException",
        "net.minecraftforge.fml.LoadingFailedException",
        "net.neoforged.fml.ModLoadingException",
        "cpw.mods.fml.common.LoaderException",
    }
)

# Regex patterns for linear line scanning
_RE_FRAME = re.compile(
    r"^\s*at\s+([a-zA-Z0-9_$./]+)\.([a-zA-Z0-9_$<>]+)\((.*?)\)(?:\s+~?\[(.*?)\])?(?:\s+\{(.*?)\})?"
)
_RE_CAUSED_BY = re.compile(r"^\s*Caused by:\s+([a-zA-Z0-9_$.]+)(?::\s*(.*))?$")
_RE_EXCEPTION = re.compile(
    r"^(?:\[[0-9:.]+\]\s*(?:\[[^]]+\]:\s*)?)?(?:Exception in thread \"[^\"]+\"\s+)?"
    r"([a-zA-Z0-9_$]+(?:\.[a-zA-Z0-9_$]+)+(?:Exception|Error|Throwable))(?::\s*(.*))?$"
)
_RE_FABRIC_MOD_ITEM = re.compile(r"^\s*-\s+([a-zA-Z0-9_-]+)\s+([^\s]+)(?:\s+\[(.*?)\])?")
_RE_CLASS_VERSION = re.compile(r"class file version (\d+)(?:\.\d+)?")

JAVA_CLASS_VERSIONS: dict[int, str] = {
    52: "Java 8",
    53: "Java 9",
    54: "Java 10",
    55: "Java 11",
    56: "Java 12",
    57: "Java 13",
    58: "Java 14",
    59: "Java 15",
    60: "Java 16",
    61: "Java 17",
    62: "Java 18",
    63: "Java 19",
    64: "Java 20",
    65: "Java 21",
    66: "Java 22",
}


def _clean_evidence_line(line: str) -> str:
    """Trim line to max allowed characters."""
    return line.strip()[:MAX_EVIDENCE_LINE_CHARS]


def _filter_platform_ids(mod_ids: list[str]) -> list[str]:
    """Filter out platform IDs from related_mod_ids."""
    return [mid for mid in mod_ids if mid and mid.lower() not in PLATFORM_IDS]


def _is_platform_jar(jar_name: str | None) -> bool:
    """Return True if jar is a Minecraft/platform/loader/bundled library jar."""
    if not jar_name:
        return True
    lower = jar_name.lower()
    if any(lower.startswith(p) for p in PLATFORM_JAR_PREFIXES):
        return True
    if lower in ("minecraft.jar", "server.jar", "client.jar"):
        return True
    return False


def _is_platform_package(class_name: str | None) -> bool:
    """Return True if class belongs to a platform or bundled library package."""
    if not class_name:
        return True
    return any(class_name.startswith(p) for p in PLATFORM_PACKAGES)


def _parse_frame_annotations(
    raw_jar_bracket: str | None,
    raw_transformer_brace: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Parse ModLauncher ~[...] and {...} frame annotations.

    Returns:
        (clean_jar_file, jar_version, clean_mixin_info)
    """
    clean_jar_file: str | None = None
    jar_version: str | None = None
    clean_mixin_info: str | None = None

    if raw_jar_bracket:
        bracket = raw_jar_bracket.strip()
        # Split on trailing :<version> if present
        if ":" in bracket:
            jar_part, ver_part = bracket.rsplit(":", 1)
            ver_part = ver_part.strip()
            if ver_part and ver_part != "?":
                jar_version = ver_part
        else:
            jar_part = bracket

        # Strip ModLauncher %23<n>!/ suffix
        # e.g. shinyblocks-2.4.1.jar%23212!/ -> shinyblocks-2.4.1.jar
        jar_part = re.sub(r"%23[^!/]*!/?$", "", jar_part.strip())
        jar_part = jar_part.rstrip("/\\")

        # Take base filename
        if "/" in jar_part or "\\" in jar_part:
            jar_base = re.split(r"[/\\]", jar_part)[-1]
        else:
            jar_base = jar_part

        # Clean any trailing %23 suffix
        jar_base = re.sub(r"%23.*", "", jar_base).strip()
        if jar_base:
            clean_jar_file = jar_base

    if raw_transformer_brace:
        brace = raw_transformer_brace.strip()
        tokens = [t.strip() for t in brace.split(",") if t.strip()]
        mixin_matches: list[str] = []
        for t in tokens:
            # Only entries of the form pl:mixin:APP:<config>.json:<MixinClass> are mixin attributions
            m = re.match(r"^pl:mixin:APP:([^:]+):(.+)$", t)
            if m:
                cfg = m.group(1).strip()
                cls = m.group(2).strip()
                mixin_matches.append(f"{cfg}:{cls}")
        if mixin_matches:
            clean_mixin_info = ", ".join(mixin_matches)

    return clean_jar_file, jar_version, clean_mixin_info


def _detect_kind_and_metadata(
    lines: list[str],
) -> tuple[CrashReportKind, str | None, str | None, str | None, str | None]:
    """Identify log kind, description, mc_version, java_version, and loader from lines."""
    kind = CrashReportKind.UNKNOWN
    description: str | None = None
    mc_version: str | None = None
    java_version: str | None = None
    loader: str | None = None

    is_hs_err = False
    is_mc_crash = False
    is_log4j = False

    for line in lines[:100]:
        lower = line.lower()
        if (
            "a fatal error has been detected by the java runtime environment" in lower
            or "# problematic frame:" in lower
        ):
            is_hs_err = True
        if "---- minecraft crash report ----" in lower:
            is_mc_crash = True
        if re.search(r"^\[\d{2}:\d{2}:\d{2}\]", line):
            is_log4j = True

    if is_hs_err:
        kind = CrashReportKind.HS_ERR
    elif is_mc_crash:
        kind = CrashReportKind.CRASH_REPORT
    elif is_log4j:
        kind = CrashReportKind.LOG_FILE
    else:
        kind = CrashReportKind.UNKNOWN

    # Scan for metadata fields and explicit loading banner
    re_loading_banner = re.compile(
        r"Loading Minecraft\s+([^\s]+)\s+with\s+(Fabric Loader|Quilt Loader|NeoForge|Forge)\s*([^\s]+)?",
        re.IGNORECASE,
    )

    for line in lines:
        s = line.strip()
        lower = s.lower()

        # Check for banner line: e.g. Loading Minecraft 1.20.1 with Fabric Loader 0.15.11
        m_banner = re_loading_banner.search(s)
        if m_banner:
            if not mc_version:
                mc_version = m_banner.group(1).strip()
            if not loader:
                l_str = m_banner.group(2).lower()
                if "fabric" in l_str:
                    loader = "fabric"
                elif "quilt" in l_str:
                    loader = "quilt"
                elif "neoforge" in l_str:
                    loader = "neoforge"
                elif "forge" in l_str:
                    loader = "forge"

        if not description and s.startswith("Description:"):
            description = s.replace("Description:", "").strip()
        elif not mc_version and (
            s.startswith("Minecraft Version:") or s.startswith("Minecraft Version ID:")
        ):
            mc_version = s.split(":", 1)[1].strip()
        elif not java_version and (s.startswith("Java Version:") or s.startswith("JRE version:")):
            java_version = s.split(":", 1)[1].strip()

        if not loader:
            if "fabric loader" in lower or "[fabricloader" in lower:
                loader = "fabric"
            elif "quilt loader" in lower or "[quiltloader" in lower:
                loader = "quilt"
            elif "neoforge" in lower or "net.neoforged" in lower:
                loader = "neoforge"
            elif "forge" in lower or "net.minecraftforge" in lower or "modsorter" in lower or "mod list:" in lower:
                loader = "forge"

    return kind, description, mc_version, java_version, loader


def _parse_exceptions(lines: list[str]) -> list[ExceptionInfo]:
    """Linear state machine parsing exception chains and stack frames."""
    blocks: list[tuple[str, str | None, list[StackFrame], bool]] = []
    active_block: tuple[str, str | None, list[StackFrame], bool] | None = None

    for idx, line in enumerate(lines, start=1):
        s = line.strip()
        if not s:
            continue

        # Check for Caused by
        m_cb = _RE_CAUSED_BY.match(s)
        if m_cb:
            if active_block:
                blocks.append(active_block)
            active_block = (m_cb.group(1), m_cb.group(2) if m_cb.group(2) else None, [], True)
            continue

        # Check for Stack frame
        m_fr = _RE_FRAME.match(s)
        if m_fr:
            if active_block:
                cls_name = m_fr.group(1)
                meth_name = m_fr.group(2)
                loc = m_fr.group(3)
                raw_jar = m_fr.group(4)
                raw_mixin = m_fr.group(5)

                jar_file, jar_ver, mixin_info = _parse_frame_annotations(raw_jar, raw_mixin)

                file_name: str | None = None
                line_no: int | None = None
                is_native = False

                if loc == "Native Method":
                    is_native = True
                elif loc != "Unknown Source" and loc:
                    if ":" in loc:
                        parts = loc.split(":")
                        file_name = parts[0]
                        try:
                            line_no = int(parts[1])
                        except ValueError:
                            pass
                    else:
                        file_name = loc

                frame = StackFrame(
                    class_name=cls_name,
                    method_name=meth_name,
                    file_name=file_name,
                    line_number=line_no,
                    jar_file=jar_file,
                    jar_version=jar_ver,
                    mixin_info=mixin_info,
                    is_native=is_native,
                    log_line_number=idx,
                )
                active_block[2].append(frame)
            continue

        # Check for new Exception header
        m_ex = _RE_EXCEPTION.match(s)
        if m_ex:
            if active_block:
                blocks.append(active_block)
            active_block = (m_ex.group(1), m_ex.group(2) if m_ex.group(2) else None, [], False)
            continue

    if active_block:
        blocks.append(active_block)

    if not blocks:
        return []

    # Reconstruct chains: group consecutive Caused by blocks into linked ExceptionInfo
    chains: list[ExceptionInfo] = []
    i = 0
    while i < len(blocks):
        chain_blocks = [blocks[i]]
        i += 1
        while i < len(blocks) and blocks[i][3]:  # is_caused_by == True
            chain_blocks.append(blocks[i])
            i += 1

        node: ExceptionInfo | None = None
        for b_cls, b_msg, b_frames, _ in reversed(chain_blocks):
            node = ExceptionInfo(
                exception_class=b_cls,
                message=b_msg,
                frames=b_frames,
                caused_by=node,
            )
        if node:
            chains.append(node)

    return chains


def _extract_mods_listed(lines: list[str]) -> list[ModEntry]:
    """Parse mod list tables from Forge/Fabric/Quilt crash reports."""
    mods: list[ModEntry] = []
    seen: set[str] = set()
    in_fabric_mods = False
    in_forge_mods = False

    for line in lines:
        s = line.strip()
        lower = s.lower()
        if "fabric mods:" in lower or "-- fabric mods --" in lower:
            in_fabric_mods = True
            in_forge_mods = False
            continue
        if "mod list:" in lower or "-- mods.toml --" in lower or "| mod id" in lower:
            in_forge_mods = True
            in_fabric_mods = False
            continue
        if in_fabric_mods and (s.startswith("--") or s.startswith("Loaded")):
            in_fabric_mods = False
        if in_forge_mods and (s.startswith("--") or s.startswith("Loaded")):
            in_forge_mods = False

        if in_fabric_mods:
            m = _RE_FABRIC_MOD_ITEM.match(s)
            if m:
                mid = m.group(1)
                ver = m.group(2)
                f_name = m.group(3)
                if mid not in seen:
                    seen.add(mid)
                    mods.append(ModEntry(id=mid, version=ver, file=f_name))
        elif in_forge_mods:
            if "|" in s:
                parts = [p.strip() for p in s.split("|")]
                if len(parts) >= 4 and parts[0].lower() not in ("file", "mod id", "mod name"):
                    f_name = parts[0]
                    name = parts[1]
                    mid = parts[2]
                    ver = parts[3]
                    if mid and mid.lower() != "mod id" and mid not in seen:
                        seen.add(mid)
                        mods.append(ModEntry(id=mid, version=ver, file=f_name))
                elif len(parts) >= 3 and parts[0].lower() not in ("file", "mod id"):
                    mid = parts[0]
                    name = parts[1]
                    ver = parts[2]
                    if mid and mid.lower() != "mod id" and mid not in seen:
                        seen.add(mid)
                        mods.append(ModEntry(id=mid, version=ver, file=None))

    return mods


def _parse_explicit_loader_statements(
    lines: list[str],
    source_label: str,
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """Extract explicit dependency resolution / missing errors from Fabric and Forge logs."""
    findings: list[Finding] = []
    suspect_candidates: list[dict[str, Any]] = []

    last_fabric_install: str | None = None
    in_forge_dep_block = False

    re_fab_missing = re.compile(
        r"^\s*-\s*Mod\s+'(?P<name>[^']+)'\s+\((?P<id>[^)]+)\)(?:\s+(?P<ver>[^\s]+))?\s+requires\s+(?P<req>.*?)\s+of\s+(?:mod\s+)?'?(?P<target>[a-zA-Z0-9_-]+)'?,\s*which is missing!",
        re.IGNORECASE,
    )
    re_fab_wrong_ver = re.compile(
        r"^\s*-\s*Mod\s+'(?P<name>[^']+)'\s+\((?P<id>[^)]+)\)(?:\s+(?P<ver>[^\s]+))?\s+requires\s+(?P<req>.*?)\s+of\s+(?:mod\s+)?'?(?P<target>[a-zA-Z0-9_-]+)'?,\s*but only the wrong version is present:\s*(?P<actual>[^!]+)!",
        re.IGNORECASE,
    )
    re_fab_breaks = re.compile(
        r"^\s*-\s*Mod\s+'(?P<name>[^']+)'\s+\((?P<id>[^)]+)\)(?:\s+(?P<ver>[^\s]+))?\s+(?:is incompatible with|breaks)\s+(?:mod\s+)?'?(?P<target>[a-zA-Z0-9_-]+)'?",
        re.IGNORECASE,
    )
    re_fab_simple_req = re.compile(
        r"^\s*-\s*Mod\s+'(?P<id>[^']+)'(?:\s+\((?P<ver>[^)]+)\))?\s+requires\s+(?P<req>.*?)\s+of\s+'?(?P<target>[a-zA-Z0-9_-]+)'?,\s*which is missing!",
        re.IGNORECASE,
    )

    re_forge_dep_line = re.compile(
        r"^\s*Mod ID:\s*'([^']+)',\s*Requested by:\s*'([^']+)',\s*Expected range:\s*'([^']*)',\s*Actual version:\s*'([^']*)'",
        re.IGNORECASE,
    )

    for idx, line in enumerate(lines, start=1):
        s = line.strip()

        # Track Fabric Install suggestion line:
        m_inst = re.match(r"^\s*-\s*Install\s+(.*)", s, re.IGNORECASE)
        if m_inst:
            last_fabric_install = f"Install {m_inst.group(1).strip()}"
            continue

        # Fabric missing requirement
        m_fm = re_fab_missing.match(s) or re_fab_simple_req.match(s)
        if m_fm:
            gd = m_fm.groupdict()
            m_id = gd.get("id") or gd.get("name", "")
            t_id = gd.get("target", "")
            m_name = gd.get("name") or m_id
            req_spec = gd.get("req", "").strip()

            sugg = last_fabric_install or f"Install '{t_id}' ({req_spec})."
            msg = f"Mod '{m_name}' ({m_id}) requires {req_spec} of '{t_id}', which is missing!"
            ev = Evidence(file=source_label, raw_value=line.strip(), message=msg)

            rel_mods = _filter_platform_ids([m_id, t_id])
            findings.append(
                Finding(
                    code=CRASH_LOADER_EXPLICIT_ISSUE,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=msg,
                    evidence=[ev],
                    related_mod_ids=rel_mods,
                    suggestion=sugg,
                )
            )
            suspect_candidates.append(
                {
                    "mod_id": m_id,
                    "name": m_name,
                    "strength": 5,
                    "attribution": "loader_statement",
                    "certainty": Certainty.CERTAIN,
                    "reason": f"Fabric explicit loader error: requires missing dependency '{t_id}' ({req_spec})",
                    "evidence_lines": [(idx, _clean_evidence_line(line))],
                    "key": f"mod:{m_id}",
                }
            )
            continue

        # Fabric wrong version requirement
        m_fw = re_fab_wrong_ver.match(s)
        if m_fw:
            gd = m_fw.groupdict()
            m_id = gd.get("id") or gd.get("name", "")
            t_id = gd.get("target", "")
            m_name = gd.get("name") or m_id
            req_spec = gd.get("req", "").strip()
            actual = gd.get("actual", "").strip()

            sugg = last_fabric_install or f"Update or replace '{t_id}' to satisfy {req_spec} (current: {actual})."
            msg = f"Mod '{m_name}' ({m_id}) requires {req_spec} of '{t_id}', but actual version is '{actual}'!"
            ev = Evidence(file=source_label, raw_value=line.strip(), message=msg)

            rel_mods = _filter_platform_ids([m_id, t_id])
            findings.append(
                Finding(
                    code=CRASH_LOADER_EXPLICIT_ISSUE,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=msg,
                    evidence=[ev],
                    related_mod_ids=rel_mods,
                    suggestion=sugg,
                )
            )
            suspect_candidates.append(
                {
                    "mod_id": m_id,
                    "name": m_name,
                    "strength": 5,
                    "attribution": "loader_statement",
                    "certainty": Certainty.CERTAIN,
                    "reason": f"Fabric explicit loader error: requires '{t_id}' {req_spec}, but found '{actual}'",
                    "evidence_lines": [(idx, _clean_evidence_line(line))],
                    "key": f"mod:{m_id}",
                }
            )
            continue

        # Fabric breaks
        m_fb = re_fab_breaks.match(s)
        if m_fb:
            gd = m_fb.groupdict()
            m_id = gd.get("id") or gd.get("name", "")
            t_id = gd.get("target", "")
            m_name = gd.get("name") or m_id

            msg = f"Mod '{m_name}' ({m_id}) is incompatible with or breaks '{t_id}'"
            sugg = f"Remove or replace conflicting mod '{t_id}'."
            ev = Evidence(file=source_label, raw_value=line.strip(), message=msg)

            rel_mods = _filter_platform_ids([m_id, t_id])
            findings.append(
                Finding(
                    code=CRASH_LOADER_EXPLICIT_ISSUE,
                    severity=Severity.ERROR,
                    certainty=Certainty.CERTAIN,
                    message=msg,
                    evidence=[ev],
                    related_mod_ids=rel_mods,
                    suggestion=sugg,
                )
            )
            suspect_candidates.append(
                {
                    "mod_id": m_id,
                    "name": m_name,
                    "strength": 5,
                    "attribution": "loader_statement",
                    "certainty": Certainty.CERTAIN,
                    "reason": f"Fabric explicit loader error: incompatible with '{t_id}'",
                    "evidence_lines": [(idx, _clean_evidence_line(line))],
                    "key": f"mod:{m_id}",
                }
            )
            continue

        # Forge / NeoForge dependency block
        if "missing or unsupported mandatory dependencies:" in s.lower():
            in_forge_dep_block = True
            continue

        if in_forge_dep_block:
            m_forge = re_forge_dep_line.match(s)
            if m_forge:
                target_id = m_forge.group(1).strip()
                requester_id = m_forge.group(2).strip()
                expected_range = m_forge.group(3).strip()
                actual_ver = m_forge.group(4).strip()

                if actual_ver.upper() == "[MISSING]":
                    msg = (
                        f"'{requester_id}' requires '{target_id}', "
                        f"expected range '{expected_range}', but it is missing"
                    )
                    sugg = f"Install '{target_id}' matching '{expected_range}'."
                else:
                    msg = (
                        f"'{requester_id}' requires '{target_id}' range '{expected_range}', "
                        f"but actual version is '{actual_ver}'"
                    )
                    sugg = f"Update or replace '{target_id}' with a version matching '{expected_range}' (currently '{actual_ver}')."

                ev = Evidence(file=source_label, raw_value=line.strip(), message=msg)
                rel_mods = _filter_platform_ids([requester_id, target_id])

                findings.append(
                    Finding(
                        code=CRASH_LOADER_EXPLICIT_ISSUE,
                        severity=Severity.ERROR,
                        certainty=Certainty.CERTAIN,
                        message=msg,
                        evidence=[ev],
                        related_mod_ids=rel_mods,
                        suggestion=sugg,
                    )
                )
                suspect_candidates.append(
                    {
                        "mod_id": requester_id,
                        "name": requester_id,
                        "strength": 5,
                        "attribution": "loader_statement",
                        "certainty": Certainty.CERTAIN,
                        "reason": f"Forge loader dependency failure: requires '{target_id}' ({expected_range})",
                        "evidence_lines": [(idx, _clean_evidence_line(line))],
                        "key": f"mod:{requester_id}",
                    }
                )
                continue
            elif s.startswith("[") or not s.startswith("Mod ID:"):
                # End of forge dep block
                in_forge_dep_block = False

    return findings, suspect_candidates


def _resolve_jar_to_mod_id(
    jar_file: str,
    mods_listed: list[ModEntry],
    scan: ScanResult | None,
) -> tuple[str | None, str]:
    """Resolve a jar file name to (mod_id | None, display_name).

    Order:
    1. Log's own mod list table
    2. Optional ScanResult
    3. Else keep jar name with mod_id = None
    """
    jar_lower = jar_file.lower()

    # 1. Check mod list table from log
    for m in mods_listed:
        if m.file:
            f_name = Path(m.file).name.lower()
            if f_name == jar_lower or jar_lower in f_name or f_name in jar_lower:
                return m.id, m.id

    # 2. Check ScanResult
    if scan:
        for m in scan.mods:
            if m.jar_path:
                j_name = Path(m.jar_path).name.lower()
                if j_name == jar_lower:
                    return m.id, m.name or m.id

    # 3. Else keep jar name with mod_id = None
    return None, jar_file


def _resolve_mixin_config_to_mod_id(
    mixin_info: str,
    mods_listed: list[ModEntry],
    scan: ScanResult | None,
) -> tuple[str | None, str]:
    """Resolve mixin config (e.g. fastrender.mixins.json) to (mod_id | None, name)."""
    config_name = mixin_info.split(":")[0].strip()
    prefix = config_name.split(".")[0].lower()

    # 1. Check mods_listed
    for m in mods_listed:
        if m.id.lower() == prefix or prefix in m.id.lower():
            return m.id, m.id
        if m.file and prefix in m.file.lower():
            return m.id, m.id

    # 2. Check ScanResult
    if scan:
        for m in scan.mods:
            if m.id.lower() == prefix:
                return m.id, m.name or m.id
            if any(prefix in f.lower() for f in m.related_files):
                return m.id, m.name or m.id

    # 3. Return prefix as candidate mod id
    return prefix, prefix


def _find_suspects_and_root(
    lines: list[str],
    exceptions: list[ExceptionInfo],
    scan: ScanResult | None,
    mods_listed: list[ModEntry],
    loader_suspect_candidates: list[dict[str, Any]],
) -> tuple[ExceptionInfo | None, list[CrashSuspect], CrashSuspect | None]:
    """Rank suspects by certainty: 1 loader_statement (CERTAIN), 2 frame/mixin (POSSIBLE), 3 heuristic."""
    suspects_by_key: dict[str, dict[str, Any]] = {}

    def add_candidate(c: dict[str, Any]) -> None:
        key = c["key"]
        if key not in suspects_by_key:
            suspects_by_key[key] = {
                "mod_id": c["mod_id"],
                "name": c["name"],
                "strength": c["strength"],
                "attribution": c["attribution"],
                "certainty": c["certainty"],
                "reason": c["reason"],
                "evidence_lines": list(c["evidence_lines"]),
                "key": key,
            }
        else:
            existing = suspects_by_key[key]
            if c["strength"] > existing["strength"]:
                existing["strength"] = c["strength"]
                existing["attribution"] = c["attribution"]
                existing["certainty"] = c["certainty"]
                existing["reason"] = c["reason"]
            if c["mod_id"] and not existing["mod_id"]:
                existing["mod_id"] = c["mod_id"]
            if c["name"] and not existing["name"]:
                existing["name"] = c["name"]
            for ev in c["evidence_lines"]:
                if ev not in existing["evidence_lines"]:
                    existing["evidence_lines"].append(ev)

    # 1. Add explicit loader statement suspects (strength 5, CERTAIN)
    for c in loader_suspect_candidates:
        add_candidate(c)

    # Determine root cause exception
    root_cause: ExceptionInfo | None = None
    if exceptions:
        root_cause = exceptions[0]
        while root_cause.caused_by is not None:
            root_cause = root_cause.caused_by

    # 2. Inspect root cause frames (strength 4 for jar, strength 2 for mixin)
    if root_cause and root_cause.frames:
        for f in root_cause.frames:
            # Check jar annotation
            if f.jar_file and not _is_platform_jar(f.jar_file):
                mid, name = _resolve_jar_to_mod_id(f.jar_file, mods_listed, scan)
                key = f"mod:{mid}" if mid else f"jar:{f.jar_file}"
                add_candidate(
                    {
                        "mod_id": mid,
                        "name": name,
                        "strength": 4,
                        "attribution": "frame_jar",
                        "certainty": Certainty.POSSIBLE,
                        "reason": f"Root-cause exception frame in '{f.jar_file}' ({f.class_name}.{f.method_name})",
                        "evidence_lines": [
                            (
                                f.log_line_number,
                                _clean_evidence_line(f"at {f.class_name}.{f.method_name} ~[{f.jar_file}]"),
                            )
                        ],
                        "key": key,
                    }
                )

            # Check mixin annotation
            if f.mixin_info:
                mid, name = _resolve_mixin_config_to_mod_id(f.mixin_info, mods_listed, scan)
                key = f"mod:{mid}" if mid else f"mixin:{f.mixin_info}"
                add_candidate(
                    {
                        "mod_id": mid,
                        "name": name,
                        "strength": 2,
                        "attribution": "mixin",
                        "certainty": Certainty.POSSIBLE,
                        "reason": f"Mixin transformer: {f.mixin_info}",
                        "evidence_lines": [
                            (
                                f.log_line_number,
                                _clean_evidence_line(f"at {f.class_name}.{f.method_name} {{{f.mixin_info}}}"),
                            )
                        ],
                        "key": key,
                    }
                )

    # 3. Inspect frames elsewhere in exception chain (strength 3 for jar, strength 2 for mixin)
    all_chain_exceptions: list[ExceptionInfo] = []
    for top_exc in exceptions:
        curr_e = top_exc
        while curr_e is not None:
            all_chain_exceptions.append(curr_e)
            curr_e = curr_e.caused_by

    for exc in all_chain_exceptions:
        if root_cause and exc is root_cause:
            continue
        for f in exc.frames:
            if root_cause and f in root_cause.frames:
                continue
            if f.jar_file and not _is_platform_jar(f.jar_file):
                mid, name = _resolve_jar_to_mod_id(f.jar_file, mods_listed, scan)
                key = f"mod:{mid}" if mid else f"jar:{f.jar_file}"
                add_candidate(
                    {
                        "mod_id": mid,
                        "name": name,
                        "strength": 3,
                        "attribution": "frame_jar",
                        "certainty": Certainty.POSSIBLE,
                        "reason": f"Exception chain frame in '{f.jar_file}' ({f.class_name}.{f.method_name})",
                        "evidence_lines": [
                            (
                                f.log_line_number,
                                _clean_evidence_line(f"at {f.class_name}.{f.method_name} ~[{f.jar_file}]"),
                            )
                        ],
                        "key": key,
                    }
                )
            if f.mixin_info:
                mid, name = _resolve_mixin_config_to_mod_id(f.mixin_info, mods_listed, scan)
                key = f"mod:{mid}" if mid else f"mixin:{f.mixin_info}"
                add_candidate(
                    {
                        "mod_id": mid,
                        "name": name,
                        "strength": 2,
                        "attribution": "mixin",
                        "certainty": Certainty.POSSIBLE,
                        "reason": f"Mixin transformer: {f.mixin_info}",
                        "evidence_lines": [
                            (
                                f.log_line_number,
                                _clean_evidence_line(f"at {f.class_name}.{f.method_name} {{{f.mixin_info}}}"),
                            )
                        ],
                        "key": key,
                    }
                )

    # 4. Package heuristic (ONLY if NO candidates found from 1-3)
    if not suspects_by_key and root_cause and root_cause.frames:
        for f in root_cause.frames:
            if not _is_platform_package(f.class_name):
                # Search mod id from package name
                matched_id: str | None = None
                pkg_lower = f.class_name.lower()
                for m in mods_listed:
                    if m.id.lower() in pkg_lower:
                        matched_id = m.id
                        break
                if not matched_id and scan:
                    for m in scan.mods:
                        if m.id.lower() in pkg_lower:
                            matched_id = m.id
                            break
                add_candidate(
                    {
                        "mod_id": matched_id,
                        "name": matched_id or f.class_name.split(".")[0],
                        "strength": 1,
                        "attribution": "package_heuristic",
                        "certainty": Certainty.INSUFFICIENT_DATA,
                        "reason": f"Heuristic package match in stack trace: {f.class_name}",
                        "evidence_lines": [
                            (
                                f.log_line_number,
                                _clean_evidence_line(f"at {f.class_name}.{f.method_name}"),
                            )
                        ],
                        "key": f"pkg:{f.class_name}",
                    }
                )
                break

    # Sort suspects: strength desc, evidence lines count desc, key asc
    sorted_candidates = sorted(
        suspects_by_key.values(),
        key=lambda s: (-s["strength"], -len(s["evidence_lines"]), str(s["mod_id"] or s["name"] or "")),
    )

    ATTRIBUTION_RANKS = {
        "loader_statement": 1,
        "frame_jar": 2,
        "mixin": 3,
        "package_heuristic": 4,
    }

    suspects: list[CrashSuspect] = []
    for c in sorted_candidates:
        r = ATTRIBUTION_RANKS.get(c["attribution"], 2)
        suspects.append(
            CrashSuspect(
                mod_id=c["mod_id"],
                name=c["name"],
                certainty=c["certainty"],
                rank=r,
                attribution=c["attribution"],
                reason=c["reason"],
                evidence_lines=c["evidence_lines"][:MAX_EVIDENCE_LINES_TOTAL],
            )
        )

    top_suspect = suspects[0] if suspects else None
    return root_cause, suspects, top_suspect


def _diagnose_signatures(
    kind: CrashReportKind,
    exceptions: list[ExceptionInfo],
    root_cause: ExceptionInfo | None,
    lines: list[str],
    source_name: str,
    top_suspect: CrashSuspect | None,
    loader_findings: list[Finding],
) -> list[Finding]:
    """Diagnose crash signatures into diagnostic findings."""
    findings: list[Finding] = []
    file_label = source_name or "crash.log"

    # Filter platform IDs from suspect's mod_id
    related_mods: list[str] = []
    if top_suspect and top_suspect.mod_id:
        related_mods = _filter_platform_ids([top_suspect.mod_id])

    # If explicit loader statements exist, they outrank generic exceptions
    if loader_findings:
        findings.extend(loader_findings)
        return findings

    # 1. Native JVM Crash (hs_err)
    if kind == CrashReportKind.HS_ERR:
        prob_frame = "Unknown problematic frame"
        for line in lines:
            if line.strip().startswith("# Problematic frame:"):
                prob_frame = line.strip().replace("# Problematic frame:", "").strip()
                break
        findings.append(
            Finding(
                code=CRASH_NATIVE_JVM,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Native JVM crash in frame: {prob_frame}",
                evidence=[Evidence(file=file_label, message=prob_frame)],
                related_mod_ids=related_mods,
                suggestion="Update graphics drivers, Java runtime, or native library dependencies (e.g. LWJGL).",
            )
        )
        return findings

    if not exceptions:
        findings.append(
            Finding(
                code=CRASH_NO_EXCEPTION_FOUND,
                severity=Severity.INFO,
                certainty=Certainty.INSUFFICIENT_DATA,
                message="No Java exceptions or loader errors detected in log",
                evidence=[Evidence(file=file_label, message="Log scan complete without detected exceptions")],
                related_mod_ids=[],
                suggestion="Inspect log file manually for application errors or abnormal termination.",
            )
        )
        return findings

    all_exc_classes = [e.exception_class for e in exceptions]
    curr = exceptions[0]
    while curr.caused_by is not None:
        curr = curr.caused_by
        all_exc_classes.append(curr.exception_class)

    exc_class = root_cause.exception_class if root_cause else exceptions[0].exception_class
    exc_msg = root_cause.message if root_cause else exceptions[0].message

    def make_ev(msg: str) -> Evidence:
        return Evidence(file=file_label, raw_value=f"{exc_class}: {exc_msg or ''}", message=msg)

    # 2. OutOfMemoryError
    if any("OutOfMemoryError" in c for c in all_exc_classes):
        findings.append(
            Finding(
                code=CRASH_OOM,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message="Java Virtual Machine ran out of memory (OutOfMemoryError)",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="Allocate more RAM (-Xmx) in your launcher settings or remove memory-heavy mods.",
            )
        )
    # 3. StackOverflowError
    elif any("StackOverflowError" in c for c in all_exc_classes):
        findings.append(
            Finding(
                code=CRASH_STACK_OVERFLOW,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message="Stack overflow encountered (StackOverflowError)",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="Check for recursive loops in entity ticks, circular world generation, or mixin recursions.",
            )
        )
    # 4. UnsupportedClassVersionError
    elif any("UnsupportedClassVersionError" in c for c in all_exc_classes):
        m_ver = _RE_CLASS_VERSION.search(exc_msg or "")
        target_java = "a newer Java version"
        if m_ver:
            c_ver = int(m_ver.group(1))
            target_java = JAVA_CLASS_VERSIONS.get(c_ver, f"class version {c_ver}")
        findings.append(
            Finding(
                code=CRASH_JAVA_VERSION_MISMATCH,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Mod compiled for {target_java} cannot run on current Java environment",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion=f"Install and configure {target_java} or newer in your Minecraft launcher profile.",
            )
        )
    # 5. ClassNotFoundException / NoClassDefFoundError
    elif any(
        c in ("java.lang.ClassNotFoundException", "java.lang.NoClassDefFoundError")
        for c in all_exc_classes
    ):
        findings.append(
            Finding(
                code=CRASH_CLASS_NOT_FOUND,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Missing class definition: {exc_msg or exc_class}",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="A required mod dependency or library is missing. Install the required dependency.",
            )
        )
    # 6. NoSuchMethodError / NoSuchFieldError
    elif any(
        c in ("java.lang.NoSuchMethodError", "java.lang.NoSuchFieldError") for c in all_exc_classes
    ):
        findings.append(
            Finding(
                code=CRASH_NO_SUCH_METHOD_FIELD,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Method or field not found: {exc_msg or exc_class}",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="Incompatible mod version. Update or downgrade the mod to match Minecraft and library versions.",
            )
        )
    # 7. Mixin errors
    elif any("Mixin" in c or "InjectionError" in c for c in all_exc_classes):
        findings.append(
            Finding(
                code=CRASH_MIXIN_FAILURE,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Mixin transformation or injection failure: {exc_msg or exc_class}",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="A mod's mixin failed to apply. Check for conflicting mods modifying the same target class.",
            )
        )
    # 8. Loader wrapper or generic unhandled exception
    elif any(c in LOADER_WRAPPER_EXCEPTIONS for c in all_exc_classes):
        # Loader wrapper exception without explicit statements
        findings.append(
            Finding(
                code=CRASH_LOADER_EXPLICIT_ISSUE,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Mod loader failed to load mods: {exc_msg or exc_class}",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="Review mod loader logs for missing mandatory dependencies or conflicting mods.",
            )
        )
    else:
        findings.append(
            Finding(
                code=CRASH_GENERIC_EXCEPTION,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Unhandled exception: {exc_class}: {exc_msg or ''}",
                evidence=[make_ev(f"{exc_class}: {exc_msg}")],
                related_mod_ids=related_mods,
                suggestion="Review stack trace frames and suspect mods for root cause.",
            )
        )

    return findings


def analyze_crash(
    data: str | bytes,
    scan: ScanResult | None = None,
    source_name: str = "",
) -> CrashAnalysisResult:
    """Analyze a crash report or log file in linear time, returning structured diagnostics."""
    is_truncated = False

    # Check size cap (20 MB)
    if isinstance(data, bytes):
        if len(data) > MAX_LOG_SIZE:
            data = data[:MAX_LOG_SIZE]
            is_truncated = True
        text = data.decode("utf-8", errors="replace")
    else:
        data_bytes = data.encode("utf-8")
        if len(data_bytes) > MAX_LOG_SIZE:
            text = data_bytes[:MAX_LOG_SIZE].decode("utf-8", errors="replace")
            is_truncated = True
        else:
            text = data

    lines = text.splitlines()

    kind, desc, mc_ver, java_ver, loader = _detect_kind_and_metadata(lines)
    exceptions = _parse_exceptions(lines)
    mods_listed = _extract_mods_listed(lines)

    loader_findings, loader_suspects = _parse_explicit_loader_statements(
        lines, source_name or "crash.log"
    )

    root_cause, suspects, top_suspect = _find_suspects_and_root(
        lines, exceptions, scan, mods_listed, loader_suspects
    )

    findings = _diagnose_signatures(
        kind, exceptions, root_cause, lines, source_name, top_suspect, loader_findings
    )

    if is_truncated:
        findings.append(
            Finding(
                code=CRASH_TRUNCATED,
                severity=Severity.WARNING,
                certainty=Certainty.CERTAIN,
                message="Log file exceeded 20 MB size limit and was truncated for analysis",
                evidence=[Evidence(file=source_name or "crash.log", message="Size cap 20 MB exceeded")],
                related_mod_ids=[],
                suggestion="Inspect full log directly if critical frames were cut off.",
            )
        )

    return CrashAnalysisResult(
        kind=kind,
        description=desc,
        mc_version=mc_ver,
        java_version=java_ver,
        detected_loader=loader,
        root_cause=root_cause,
        exceptions=exceptions,
        suspects=suspects,
        findings=findings,
        mods_listed=mods_listed,
        is_truncated=is_truncated,
        top_suspect=top_suspect,
    )
