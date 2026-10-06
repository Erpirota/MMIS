"""Minecraft layer for MMIS.

Imports domain models, provides parsers, archive reading, and scanner orchestrator.
"""

from mmis.minecraft.fabric import parse_fabric_mod_json
from mmis.minecraft.forge import parse_forge_toml
from mmis.minecraft.jar import read_jar
from mmis.minecraft.pack_manifests import detect_pack_manifest
from mmis.minecraft.quilt import parse_quilt_mod_json
from mmis.minecraft.scanner import scan

__all__ = [
    "detect_pack_manifest",
    "parse_fabric_mod_json",
    "parse_forge_toml",
    "parse_quilt_mod_json",
    "read_jar",
    "scan",
]
