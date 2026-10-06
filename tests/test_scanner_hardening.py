"""Tests for scanner hardening, metadata size limits, streaming I/O, resource safety, and error handling."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from mmis.domain.findings import (
    CORRUPT_JAR,
    INVALID_METADATA_JSON,
    INVALID_METADATA_TOML,
    METADATA_TOO_LARGE,
    NO_METADATA,
    NOT_A_ZIP,
    PACK_MANIFEST_DETECTED,
)
from mmis.domain.models import Certainty, Loader, Severity
from mmis.minecraft.jar import MAX_METADATA_SIZE_BYTES, read_jar
from mmis.minecraft.scanner import scan
from tests.helpers import create_jar, create_nested_jar_bytes


# 1. Each of the 4 descriptor kinds: exactly-at-limit accepted, limit+1 refused
@pytest.mark.parametrize(
    ("entry_name", "prefix", "suffix", "mod_id"),
    [
        (
            "fabric.mod.json",
            b'{"schemaVersion": 1, "id": "fab_exact", "description": "',
            b'"}',
            "fab_exact",
        ),
        (
            "quilt.mod.json",
            b'{"schemaVersion": 1, "quilt_loader": {"id": "quilt_exact", "version": "1.0", "metadata": {"description": "',
            b'"}}}',
            "quilt_exact",
        ),
        (
            "META-INF/neoforge.mods.toml",
            b'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="nf_exact"\nversion="1.0"\ndescription="',
            b'"\n',
            "nf_exact",
        ),
        (
            "META-INF/mods.toml",
            b'modLoader="javafml"\nloaderVersion="[1,)"\n[[mods]]\nmodId="forge_exact"\nversion="1.0"\ndescription="',
            b'"\n',
            "forge_exact",
        ),
    ],
)
def test_descriptor_exact_limit_and_exceeded(entry_name: str, prefix: bytes, suffix: bytes, mod_id: str):
    # A) Exactly at limit: accepted and parsed
    pad_len_exact = MAX_METADATA_SIZE_BYTES - len(prefix) - len(suffix)
    content_exact = prefix + (b"x" * pad_len_exact) + suffix
    assert len(content_exact) == MAX_METADATA_SIZE_BYTES

    jar_exact_bytes = create_nested_jar_bytes({entry_name: content_exact})
    j_file, mods, findings, _ = read_jar(jar_exact_bytes, "mods/exact.jar")

    assert len(mods) == 1
    assert mods[0].id == mod_id
    assert not any(f.code == METADATA_TOO_LARGE for f in findings)
    assert not any(f.code in (NO_METADATA, CORRUPT_JAR) for f in findings)

    # B) Limit + 1: refused with METADATA_TOO_LARGE and no mod
    pad_len_oversized = pad_len_exact + 1
    content_oversized = prefix + (b"x" * pad_len_oversized) + suffix
    assert len(content_oversized) == MAX_METADATA_SIZE_BYTES + 1

    jar_oversized_bytes = create_nested_jar_bytes({entry_name: content_oversized})
    j_file_o, mods_o, findings_o, _ = read_jar(jar_oversized_bytes, "mods/oversized.jar")

    assert len(mods_o) == 0
    oversized_findings = [f for f in findings_o if f.code == METADATA_TOO_LARGE]
    assert len(oversized_findings) == 1
    f = oversized_findings[0]
    assert f.severity == Severity.ERROR
    assert f.certainty == Certainty.CERTAIN
    assert len(f.evidence) == 1
    assert f.evidence[0].file == "mods/oversized.jar"
    assert f.evidence[0].field == entry_name
    assert f.evidence[0].raw_value == str(MAX_METADATA_SIZE_BYTES + 1)
    assert not any(f.code in (NO_METADATA, CORRUPT_JAR) for f in findings_o)


# 2. Highly compressible oversized descriptor (64 MiB spaces): parser spy proves it is never called
def test_highly_compressible_oversized_descriptor(monkeypatch: pytest.MonkeyPatch):
    import mmis.minecraft.jar as jar_mod

    parser_calls: list[bytes] = []
    orig_parse = jar_mod.parse_fabric_mod_json

    def spy_parse(content, *args, **kwargs):
        parser_calls.append(content)
        return orig_parse(content, *args, **kwargs)

    monkeypatch.setattr(jar_mod, "parse_fabric_mod_json", spy_parse)

    # 64 MiB payload compresses to very small zip
    spaces_len = 64 * 1024 * 1024
    huge_json = b'{"schemaVersion": 1, "id": "huge"} ' + (b" " * spaces_len)
    jar_bytes = create_nested_jar_bytes({"fabric.mod.json": huge_json})

    j_file, mods, findings, _ = read_jar(jar_bytes, "mods/huge.jar")

    assert len(mods) == 0
    assert len(parser_calls) == 0  # Parser was never called!
    too_large = [f for f in findings if f.code == METADATA_TOO_LARGE]
    assert len(too_large) == 1
    assert too_large[0].severity == Severity.ERROR
    assert too_large[0].evidence[0].raw_value == str(len(huge_json))


# 3. Oversized MANIFEST.MF with mods.toml using version "${file.jarVersion}"
def test_oversized_manifest_with_mods_toml():
    manifest_content = b"Implementation-Version: 2.5.0\n" + (b" " * (MAX_METADATA_SIZE_BYTES + 1024))
    mods_toml = """
    modLoader = "javafml"
    loaderVersion = "[1,)"
    [[mods]]
    modId = "test_manifest_mod"
    version = "${file.jarVersion}"
    """
    jar_bytes = create_nested_jar_bytes({
        "META-INF/MANIFEST.MF": manifest_content,
        "META-INF/mods.toml": mods_toml,
    })

    j_file, mods, findings, _ = read_jar(jar_bytes, "mods/manifest_test.jar")

    assert len(mods) == 1
    assert mods[0].id == "test_manifest_mod"
    assert mods[0].version != "2.5.0"
    assert mods[0].version is None

    # Finding for MANIFEST.MF must have Severity.WARNING
    manifest_findings = [f for f in findings if f.code == METADATA_TOO_LARGE]
    assert len(manifest_findings) == 1
    assert manifest_findings[0].severity == Severity.WARNING
    assert manifest_findings[0].certainty == Certainty.CERTAIN
    assert manifest_findings[0].evidence[0].field == "META-INF/MANIFEST.MF"


# 4. Each of the 3 pack manifests oversized: refused, nothing derived, jars still scanned
@pytest.mark.parametrize(
    "manifest_name",
    ["manifest.json", "modrinth.index.json", "mmc-pack.json"],
)
def test_oversized_pack_manifests(tmp_path: Path, manifest_name: str):
    pack_dir = tmp_path / f"pack_{manifest_name}"
    pack_dir.mkdir(parents=True)

    # Healthy jar in mods/
    healthy_jar = create_jar(
        pack_dir / "mods" / "healthy.jar",
        {"fabric.mod.json": '{"schemaVersion": 1, "id": "healthy_mod"}'},
    )

    # Oversized pack manifest (> 4 MiB)
    manifest_file = pack_dir / manifest_name
    oversized_size = MAX_METADATA_SIZE_BYTES + 512
    with open(manifest_file, "wb") as f:
        f.write(b"{\n")
        f.write(b" " * (oversized_size - 4))
        f.write(b"\n}")
    assert manifest_file.stat().st_size == oversized_size

    res = scan(str(pack_dir))

    # METADATA_TOO_LARGE emitted for the pack manifest
    manifest_too_large = [
        f for f in res.findings
        if f.code == METADATA_TOO_LARGE and any(e.file == manifest_name for e in f.evidence)
    ]
    assert len(manifest_too_large) == 1
    assert manifest_too_large[0].severity == Severity.ERROR
    assert manifest_too_large[0].certainty == Certainty.CERTAIN

    # PACK_MANIFEST_DETECTED must NOT be emitted
    assert not any(f.code == PACK_MANIFEST_DETECTED for f in res.findings)

    # Jar is still scanned and mod parsed
    assert any(m.id == "healthy_mod" for m in res.mods)


# 5. read_jar given a path and given the bytes of the same file returns equal results
def test_read_jar_path_vs_bytes(tmp_path: Path):
    jar_file = create_jar(
        tmp_path / "sample.jar",
        {
            "fabric.mod.json": '{"schemaVersion": 1, "id": "sample_mod", "version": "1.2.3"}',
            "META-INF/MANIFEST.MF": "Implementation-Version: 1.2.3\n",
        },
    )
    jar_bytes = jar_file.read_bytes()

    res_path = read_jar(jar_file, "mods/sample.jar")
    res_bytes = read_jar(jar_bytes, "mods/sample.jar")

    # JarFile equal
    assert res_path[0] == res_bytes[0]
    assert res_path[0].sha256 == res_bytes[0].sha256
    assert res_path[0].size_bytes == res_bytes[0].size_bytes
    # Mods equal
    assert res_path[1] == res_bytes[1]
    # Findings equal
    assert res_path[2] == res_bytes[2]
    # Nested equal
    assert res_path[3] == res_bytes[3]


# 6. After scan(), the jar file can be deleted (handles released on Windows)
def test_handles_released_after_scan(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(parents=True)

    valid_jar = create_jar(
        mods_dir / "valid.jar",
        {"fabric.mod.json": '{"schemaVersion": 1, "id": "valid_mod"}'},
    )

    corrupt_jar = mods_dir / "corrupt.jar"
    corrupt_jar.write_bytes(b"PK\x03\x04\x00\x00corrupt_truncated")

    notazip_jar = mods_dir / "notazip.jar"
    notazip_jar.write_bytes(b"This is a plain text file, not a zip.")

    res = scan(str(tmp_path))

    # All files must be deletable without Windows sharing violation
    valid_jar.unlink()
    assert not valid_jar.exists()

    corrupt_jar.unlink()
    assert not corrupt_jar.exists()

    notazip_jar.unlink()
    assert not notazip_jar.exists()


# 7. Corrupt inputs produce controlled findings and no exception
def test_corrupt_inputs_controlled_findings(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(parents=True)

    # 1. Truncated zip
    p1 = mods_dir / "p1_trunc.jar"
    p1.write_bytes(b"PK\x03\x04truncated")

    # 2. Random bytes
    p2 = mods_dir / "p2_random.jar"
    p2.write_bytes(b"\x99\x88\x77\x66\x55" * 20)

    # 3. Zero-byte file
    p3 = mods_dir / "p3_zero.jar"
    p3.write_bytes(b"")

    # 4. Bad CRC entry
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("fabric.mod.json", b"hello world 1234567890")
    bad_crc_raw = bytearray(buf.getvalue())
    idx = bad_crc_raw.find(b"hello world")
    assert idx != -1
    bad_crc_raw[idx] = ord("X")  # Invalidate CRC
    p4 = mods_dir / "p4_badcrc.jar"
    p4.write_bytes(bytes(bad_crc_raw))

    # 5. Malformed JSON
    p5 = create_jar(mods_dir / "p5_badjson.jar", {"fabric.mod.json": "{\nnot json: true"})

    # 6. Malformed TOML
    p6 = create_jar(mods_dir / "p6_badtoml.jar", {"META-INF/mods.toml": "[[mods\nnot toml"})

    # 7. Binary garbage as descriptor
    p7 = create_jar(mods_dir / "p7_binjson.jar", {"fabric.mod.json": b"\x00\xff\xfe\x01\x02"})

    # 8. JSON of the wrong shape (a list)
    p8 = create_jar(mods_dir / "p8_listjson.jar", {"fabric.mod.json": "[1, 2, 3]"})

    # 9. A directory named x.jar (skipped silently by scanner)
    p9 = mods_dir / "x.jar"
    p9.mkdir()

    # None of these should raise any exception during scan()
    res = scan(str(tmp_path))
    assert res is not None

    # Directory named x.jar is silently skipped: no JarFile, no finding
    assert not any(j.path.endswith("x.jar") for j in res.jars)
    assert not any("x.jar" in f.message or any("x.jar" in (e.file or "") for e in f.evidence) for f in res.findings)

    finding_codes = {f.code for f in res.findings}
    assert CORRUPT_JAR in finding_codes
    assert NOT_A_ZIP in finding_codes
    assert INVALID_METADATA_JSON in finding_codes
    assert INVALID_METADATA_TOML in finding_codes

    # Direct call to read_jar on a directory or unopenable path returns controlled CORRUPT_JAR
    j_dir, mods_dir_res, findings_dir, _ = read_jar(p9, "mods/x.jar")
    assert any(f.code == CORRUPT_JAR for f in findings_dir)
    assert len(mods_dir_res) == 0

    j_unreadable, mods_unreadable, findings_unreadable, _ = read_jar(mods_dir / "non_existent.jar", "mods/non_existent.jar")
    assert any(f.code == CORRUPT_JAR for f in findings_unreadable)
    assert len(mods_unreadable) == 0


# 8. Nested jar with an oversized descriptor: finding carries the nested display path, sibling still parsed
def test_nested_jar_oversized_descriptor(tmp_path: Path):
    oversized_json = b'{"schemaVersion": 1, "id": "sub_over", "desc": "' + (b"x" * MAX_METADATA_SIZE_BYTES) + b'"}'
    sub_over_bytes = create_nested_jar_bytes({"fabric.mod.json": oversized_json})

    healthy_json = '{"schemaVersion": 1, "id": "sub_healthy", "name": "Sub Healthy"}'
    sub_healthy_bytes = create_nested_jar_bytes({"fabric.mod.json": healthy_json})

    parent_bytes = create_nested_jar_bytes({
        "fabric.mod.json": json.dumps({
            "schemaVersion": 1,
            "id": "parent_mod",
            "jars": [
                {"file": "META-INF/jars/sub_over.jar"},
                {"file": "META-INF/jars/sub_healthy.jar"},
            ],
        }),
        "META-INF/jars/sub_over.jar": sub_over_bytes,
        "META-INF/jars/sub_healthy.jar": sub_healthy_bytes,
    })

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(parents=True)
    parent_path = mods_dir / "parent.jar"
    parent_path.write_bytes(parent_bytes)

    res = scan(str(tmp_path))

    # Oversized nested jar finding carries the nested display path
    too_large = [f for f in res.findings if f.code == METADATA_TOO_LARGE]
    assert len(too_large) == 1
    ev = too_large[0].evidence[0]
    assert ev.file == "mods/parent.jar!/META-INF/jars/sub_over.jar"
    assert ev.field == "fabric.mod.json"

    # Sibling healthy nested jar is still parsed
    mod_ids = [m.id for m in res.mods]
    assert "parent_mod" in mod_ids
    assert "sub_healthy" in mod_ids
    assert "sub_over" not in mod_ids


# 9. Determinism: two scans of the same pathological pack give identical ScanResult.to_dict()
def test_pathological_pack_scan_determinism(tmp_path: Path):
    pack_dir = tmp_path / "patho_pack"
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir(parents=True)

    # Valid jar
    create_jar(
        mods_dir / "a_valid.jar",
        {"fabric.mod.json": '{"schemaVersion": 1, "id": "mod_a"}'},
    )

    # Corrupt zip
    (mods_dir / "b_corrupt.jar").write_bytes(b"PK\x03\x04truncated")

    # Non-zip
    (mods_dir / "c_text.jar").write_bytes(b"not a zip")

    # Directory named jar
    (mods_dir / "d_dir.jar").mkdir()

    # Oversized manifest
    manifest_file = pack_dir / "manifest.json"
    with open(manifest_file, "wb") as f:
        f.write(b"{\n" + (b" " * (MAX_METADATA_SIZE_BYTES + 100)) + b"}")

    res1 = scan(str(pack_dir))
    res2 = scan(str(pack_dir))

    assert res1.to_dict() == res2.to_dict()


# 10. Regression tests for damaged deflate stream (F1)
def _make_damaged_deflate_jar_bytes() -> bytes:
    """Build a zip with fabric.mod.json + assets/a.txt and a damaged deflate stream."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("fabric.mod.json", b'{"schemaVersion": 1, "id": "damaged_mod"}')
        zf.writestr("assets/a.txt", b"hello" * 2000)
    data = bytearray(buf.getvalue())
    needle = b"assets/a.txt"
    idx = data.find(needle)
    assert idx != -1
    data[idx + len(needle) + 16] ^= 0xFF
    return bytes(data)


def test_damaged_deflate_stream_top_level(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir(parents=True)
    d_jar = mods_dir / "d.jar"
    d_jar.write_bytes(_make_damaged_deflate_jar_bytes())

    res = scan(str(tmp_path))

    # Controlled CORRUPT_JAR finding emitted, no exception raised
    corrupt = [f for f in res.findings if f.code == CORRUPT_JAR and any("d.jar" in (e.file or "") for e in f.evidence)]
    assert len(corrupt) == 1
    assert corrupt[0].severity == Severity.ERROR
    assert corrupt[0].certainty == Certainty.CERTAIN

    # JarFile listed with mods == []
    matching_jars = [j for j in res.jars if j.path.endswith("d.jar")]
    assert len(matching_jars) == 1
    assert matching_jars[0].mods == []

    # Handle released, deletable
    d_jar.unlink()
    assert not d_jar.exists()


def test_damaged_deflate_stream_next_to_healthy(tmp_path: Path):
    pack_dir = tmp_path / "pack_damaged_and_healthy"
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir(parents=True)

    # Damaged jar
    (mods_dir / "d.jar").write_bytes(_make_damaged_deflate_jar_bytes())

    # Healthy jar
    create_jar(
        mods_dir / "healthy.jar",
        {"fabric.mod.json": '{"schemaVersion": 1, "id": "healthy_mod"}'},
    )

    res = scan(str(pack_dir))

    # Damaged jar has controlled CORRUPT_JAR finding
    assert any(f.code == CORRUPT_JAR and any("d.jar" in (e.file or "") for e in f.evidence) for f in res.findings)

    # Healthy jar is still parsed
    mod_ids = [m.id for m in res.mods]
    assert "healthy_mod" in mod_ids
    assert "damaged_mod" not in mod_ids


def test_damaged_deflate_stream_nested(tmp_path: Path):
    pack_dir = tmp_path / "pack_nested_damaged"
    mods_dir = pack_dir / "mods"
    mods_dir.mkdir(parents=True)

    damaged_bytes = _make_damaged_deflate_jar_bytes()
    healthy_sub_bytes = create_nested_jar_bytes({
        "fabric.mod.json": '{"schemaVersion": 1, "id": "healthy_sub"}'
    })

    parent_bytes = create_nested_jar_bytes({
        "fabric.mod.json": json.dumps({
            "schemaVersion": 1,
            "id": "parent_mod",
            "jars": [
                {"file": "META-INF/jars/damaged_child.jar"},
                {"file": "META-INF/jars/healthy_child.jar"},
            ],
        }),
        "META-INF/jars/damaged_child.jar": damaged_bytes,
        "META-INF/jars/healthy_child.jar": healthy_sub_bytes,
    })

    (mods_dir / "parent.jar").write_bytes(parent_bytes)

    res = scan(str(pack_dir))

    # Damaged child emits CORRUPT_JAR with nested display path
    nested_corrupt = [
        f for f in res.findings
        if f.code == CORRUPT_JAR and any("damaged_child.jar" in (e.file or "") for e in f.evidence)
    ]
    assert len(nested_corrupt) == 1
    assert "parent.jar!/META-INF/jars/damaged_child.jar" in nested_corrupt[0].evidence[0].file

    # Healthy child and parent mod still parsed
    mod_ids = [m.id for m in res.mods]
    assert "parent_mod" in mod_ids
    assert "healthy_sub" in mod_ids
