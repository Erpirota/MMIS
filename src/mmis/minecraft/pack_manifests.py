"""Pack manifest detection for CurseForge, Modrinth, and MultiMC/Prism.

Pure stdlib, pure functions.
"""

from __future__ import annotations

import json
import os
from typing import Any

from mmis.domain.findings import (
    CONFLICTING_MANIFEST,
    INVALID_PACK_MANIFEST,
    METADATA_TOO_LARGE,
    PACK_MANIFEST_DETECTED,
)
from mmis.domain.models import (
    Certainty,
    Evidence,
    Finding,
    Loader,
    Severity,
)
from mmis.minecraft.jar import MAX_METADATA_SIZE_BYTES


def _loader_from_str(s: str) -> Loader | None:
    """Parse loader enum from string identifier."""
    lower = s.lower()
    if "neoforge" in lower:
        return Loader.NEOFORGE
    if "forge" in lower:
        return Loader.FORGE
    if "quilt" in lower:
        return Loader.QUILT
    if "fabric" in lower:
        return Loader.FABRIC
    return None


def _check_manifest_file(file_path: str, rel_path: str, manifest_kind: str) -> list[Finding] | None:
    """Validate manifest file accessibility and size limit before reading.

    Returns a list of findings if the file is inaccessible or oversized, else None.
    """
    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        return [
            Finding(
                code=INVALID_PACK_MANIFEST,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to access {manifest_kind} {rel_path}: {exc}",
                evidence=[Evidence(file=rel_path, message=str(exc))],
            )
        ]

    if size > MAX_METADATA_SIZE_BYTES:
        return [
            Finding(
                code=METADATA_TOO_LARGE,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Pack manifest {rel_path} exceeds size limit of {MAX_METADATA_SIZE_BYTES} bytes",
                evidence=[
                    Evidence(
                        file=rel_path,
                        raw_value=str(size),
                        message=f"Manifest file exceeds size limit of {MAX_METADATA_SIZE_BYTES} bytes",
                    )
                ],
            )
        ]

    return None


def detect_curseforge(file_path: str, rel_path: str) -> tuple[Loader | None, str | None, list[Finding], list[Evidence]]:
    """Parse CurseForge manifest.json."""
    findings: list[Finding] = []
    evidence: list[Evidence] = []
    loader: Loader | None = None
    mc_version: str | None = None

    check_findings = _check_manifest_file(file_path, rel_path, "CurseForge manifest")
    if check_findings is not None:
        return None, None, check_findings, []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        findings.append(
            Finding(
                code=INVALID_PACK_MANIFEST,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to parse CurseForge manifest {rel_path}: {exc}",
                evidence=[Evidence(file=rel_path, message=str(exc))],
            )
        )
        return None, None, findings, evidence

    if not isinstance(data, dict):
        return None, None, findings, evidence

    minecraft_sec = data.get("minecraft")
    if isinstance(minecraft_sec, dict):
        raw_mc = minecraft_sec.get("version")
        if raw_mc is not None:
            mc_version = str(raw_mc)
            evidence.append(
                Evidence(
                    file=rel_path,
                    field="minecraft.version",
                    raw_value=mc_version,
                    message="Detected Minecraft version from CurseForge manifest",
                )
            )

        mod_loaders = minecraft_sec.get("modLoaders")
        if isinstance(mod_loaders, list) and mod_loaders:
            primary_entry = next((e for e in mod_loaders if isinstance(e, dict) and e.get("primary")), mod_loaders[0])
            if isinstance(primary_entry, dict) and "id" in primary_entry:
                loader_id = str(primary_entry["id"])
                loader = _loader_from_str(loader_id)
                evidence.append(
                    Evidence(
                        file=rel_path,
                        field="minecraft.modLoaders",
                        raw_value=loader_id,
                        message=f"Detected mod loader from CurseForge manifest: {loader_id}",
                    )
                )

    if loader is not None or mc_version is not None:
        findings.append(
            Finding(
                code=PACK_MANIFEST_DETECTED,
                severity=Severity.INFO,
                certainty=Certainty.CERTAIN,
                message=f"CurseForge pack manifest detected at {rel_path}",
                evidence=evidence or [Evidence(file=rel_path, message="CurseForge manifest")],
            )
        )

    return loader, mc_version, findings, evidence


def detect_modrinth(file_path: str, rel_path: str) -> tuple[Loader | None, str | None, list[Finding], list[Evidence]]:
    """Parse Modrinth modrinth.index.json."""
    findings: list[Finding] = []
    evidence: list[Evidence] = []
    loader: Loader | None = None
    mc_version: str | None = None

    check_findings = _check_manifest_file(file_path, rel_path, "Modrinth index")
    if check_findings is not None:
        return None, None, check_findings, []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        findings.append(
            Finding(
                code=INVALID_PACK_MANIFEST,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to parse Modrinth index {rel_path}: {exc}",
                evidence=[Evidence(file=rel_path, message=str(exc))],
            )
        )
        return None, None, findings, evidence

    if not isinstance(data, dict):
        return None, None, findings, evidence

    deps = data.get("dependencies")
    if isinstance(deps, dict):
        if "minecraft" in deps:
            mc_version = str(deps["minecraft"])
            evidence.append(
                Evidence(
                    file=rel_path,
                    field="dependencies.minecraft",
                    raw_value=mc_version,
                    message="Detected Minecraft version from Modrinth index",
                )
            )

        for dep_key in deps:
            l = _loader_from_str(dep_key)
            if l is not None:
                loader = l
                evidence.append(
                    Evidence(
                        file=rel_path,
                        field=f"dependencies.{dep_key}",
                        raw_value=str(deps[dep_key]),
                        message=f"Detected mod loader from Modrinth index: {dep_key}",
                    )
                )
                break

    if loader is not None or mc_version is not None:
        findings.append(
            Finding(
                code=PACK_MANIFEST_DETECTED,
                severity=Severity.INFO,
                certainty=Certainty.CERTAIN,
                message=f"Modrinth pack manifest detected at {rel_path}",
                evidence=evidence or [Evidence(file=rel_path, message="Modrinth index")],
            )
        )

    return loader, mc_version, findings, evidence


def detect_multimc(file_path: str, rel_path: str) -> tuple[Loader | None, str | None, list[Finding], list[Evidence]]:
    """Parse MultiMC/Prism mmc-pack.json."""
    findings: list[Finding] = []
    evidence: list[Evidence] = []
    loader: Loader | None = None
    mc_version: str | None = None

    check_findings = _check_manifest_file(file_path, rel_path, "MultiMC/Prism pack")
    if check_findings is not None:
        return None, None, check_findings, []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        findings.append(
            Finding(
                code=INVALID_PACK_MANIFEST,
                severity=Severity.ERROR,
                certainty=Certainty.CERTAIN,
                message=f"Failed to parse MultiMC/Prism pack {rel_path}: {exc}",
                evidence=[Evidence(file=rel_path, message=str(exc))],
            )
        )
        return None, None, findings, evidence

    if not isinstance(data, dict):
        return None, None, findings, evidence

    components = data.get("components")
    if isinstance(components, list):
        for comp in components:
            if not isinstance(comp, dict):
                continue
            uid = str(comp.get("uid", ""))
            v = comp.get("version")
            v_str = str(v) if v is not None else None

            if uid == "net.minecraft" and v_str:
                mc_version = v_str
                evidence.append(
                    Evidence(
                        file=rel_path,
                        field="components[net.minecraft].version",
                        raw_value=v_str,
                        message="Detected Minecraft version from mmc-pack.json",
                    )
                )

            l = _loader_from_str(uid)
            if l is not None:
                loader = l
                evidence.append(
                    Evidence(
                        file=rel_path,
                        field=f"components[{uid}].version",
                        raw_value=v_str,
                        message=f"Detected mod loader from mmc-pack.json: {uid}",
                    )
                )

    if loader is not None or mc_version is not None:
        findings.append(
            Finding(
                code=PACK_MANIFEST_DETECTED,
                severity=Severity.INFO,
                certainty=Certainty.CERTAIN,
                message=f"MultiMC/Prism pack manifest detected at {rel_path}",
                evidence=evidence or [Evidence(file=rel_path, message="MultiMC pack manifest")],
            )
        )

    return loader, mc_version, findings, evidence


def detect_pack_manifest(root_dir: str) -> tuple[Loader | None, str | None, list[Finding]]:
    """Scan root_dir for pack manifests in priority order."""
    all_findings: list[Finding] = []
    detected_results: list[tuple[str, Loader | None, str | None, list[Evidence]]] = []

    # Check for CurseForge manifest.json
    cf_path = os.path.join(root_dir, "manifest.json")
    if os.path.isfile(cf_path):
        l, mc, f, ev = detect_curseforge(cf_path, "manifest.json")
        all_findings.extend(f)
        if l or mc:
            detected_results.append(("manifest.json", l, mc, ev))

    # Check for Modrinth modrinth.index.json
    mr_path = os.path.join(root_dir, "modrinth.index.json")
    if os.path.isfile(mr_path):
        l, mc, f, ev = detect_modrinth(mr_path, "modrinth.index.json")
        all_findings.extend(f)
        if l or mc:
            detected_results.append(("modrinth.index.json", l, mc, ev))

    # Check for MultiMC/Prism mmc-pack.json
    mmc_path = os.path.join(root_dir, "mmc-pack.json")
    if os.path.isfile(mmc_path):
        l, mc, f, ev = detect_multimc(mmc_path, "mmc-pack.json")
        all_findings.extend(f)
        if l or mc:
            detected_results.append(("mmc-pack.json", l, mc, ev))

    if not detected_results:
        return None, None, all_findings

    # Check for conflicting manifests
    first_file, chosen_loader, chosen_mc, chosen_ev = detected_results[0]
    for other_file, other_loader, other_mc, other_ev in detected_results[1:]:
        conflict_fields = []
        if chosen_loader and other_loader and chosen_loader != other_loader:
            conflict_fields.append(f"Loader ({chosen_loader.value} vs {other_loader.value})")
        if chosen_mc and other_mc and chosen_mc != other_mc:
            conflict_fields.append(f"Minecraft version ({chosen_mc} vs {other_mc})")

        if conflict_fields:
            all_findings.append(
                Finding(
                    code=CONFLICTING_MANIFEST,
                    severity=Severity.WARNING,
                    certainty=Certainty.CERTAIN,
                    message=f"Conflicting pack manifests between {first_file} and {other_file}: {', '.join(conflict_fields)}",
                    evidence=chosen_ev + other_ev,
                )
            )

    return chosen_loader, chosen_mc, all_findings
