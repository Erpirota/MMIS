"""Master acceptance check for M7-1 (scanner hardening).

Usage: check_m7_scanner.py <work_dir>   (project venv python, PYTHONPATH=src, cwd = repo)

<work_dir> must be an empty scratch directory outside the repository. The scan
of the build_pack.py fixture is compared with golden_m7_scan.json, recorded
before the hardening: results for well-formed packs must not change.
"""
import hashlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import time
import tracemalloc
import zipfile
from pathlib import Path

from mmis.domain import findings as F
from mmis.minecraft import jar as jar_mod
from mmis.minecraft.jar import read_jar
from mmis.minecraft.scanner import scan

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden_m7_scan.json"
work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
MIB = 1024 * 1024
failed = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failed.append(name)
    return bool(cond)


def pack(name):
    d = work / name / "mods"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_jar(path, entries, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression) as z:
        for n, data in entries.items():
            z.writestr(n, data)


def codes(result):
    return [f.code for f in result.findings]


def measured_scan(target):
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        r = scan(str(target))
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return r, peak / MIB, time.perf_counter() - t0


FABRIC = json.dumps({"schemaVersion": 1, "id": "padded", "version": "1.0.0"})
QUILT = json.dumps({"schema_version": 1, "quilt_loader": {"group": "g", "id": "padded", "version": "1.0.0"}})
TOML = 'modLoader="javafml"\nloaderVersion="[40,)"\nlicense="MIT"\n[[mods]]\nmodId="padded"\nversion="1.0.0"\n'
DESCRIPTORS = {
    "fabric.mod.json": FABRIC,
    "quilt.mod.json": QUILT,
    "META-INF/mods.toml": TOML,
    "META-INF/neoforge.mods.toml": TOML,
}

# --- 1. API surface -------------------------------------------------------
check("merge_dual removed from read_jar", "merge_dual" not in inspect.signature(read_jar).parameters)
left = subprocess.run(["git", "grep", "-n", "merge_dual", "--", "src", "tests"], capture_output=True, text=True).stdout.strip()
check("no merge_dual reference left in src/ and tests/", not left, left[:200])
LIMIT = getattr(jar_mod, "MAX_METADATA_SIZE_BYTES", None)
check("MAX_METADATA_SIZE_BYTES is 4 MiB", LIMIT == 4 * MIB, f"value={LIMIT}")
TOO_LARGE = getattr(F, "METADATA_TOO_LARGE", None)
check("finding code METADATA_TOO_LARGE defined", TOO_LARGE == "METADATA_TOO_LARGE", f"value={TOO_LARGE}")
check("METADATA_TOO_LARGE mapped to metadata category", F.CODE_TO_CATEGORY.get("METADATA_TOO_LARGE") == F.CATEGORY_METADATA)
if LIMIT is None or TOO_LARGE is None:
    print("FAILED:", failed)
    sys.exit(1)

# --- 2. Size limit, one descriptor kind at a time ---------------------------
for entry, body in DESCRIPTORS.items():
    tag = entry.split("/")[-1]
    exact = body + " " * (LIMIT - len(body))
    d = pack(f"exact_{tag}")
    write_jar(d / "m.jar", {entry: exact})
    r = scan(str(d.parent))
    check(f"{tag}: exactly at the limit is parsed", [m.id for m in r.mods] == ["padded"] and TOO_LARGE not in codes(r), f"mods={[m.id for m in r.mods]} codes={codes(r)}")

    d = pack(f"over_{tag}")
    write_jar(d / "m.jar", {entry: exact + " "})
    r = scan(str(d.parent))
    hits = [f for f in r.findings if f.code == TOO_LARGE]
    check(f"{tag}: one byte over the limit is refused", len(hits) == 1 and not r.mods, f"mods={[m.id for m in r.mods]} codes={codes(r)}")
    check(f"{tag}: refusal is not reported as missing or invalid metadata",
          not {F.NO_METADATA, F.INVALID_METADATA_JSON, F.INVALID_METADATA_TOML, F.CORRUPT_JAR} & set(codes(r)), f"codes={codes(r)}")
    if hits:
        ev = hits[0].evidence
        check(f"{tag}: finding names the jar and the entry",
              bool(ev) and ev[0].file == "mods/m.jar" and (ev[0].field or "") == entry, f"evidence={[(e.file, e.field) for e in ev]}")
        check(f"{tag}: finding is an explicit certain error", hits[0].severity.value == "error" and hits[0].certainty.value == "certain",
              f"{hits[0].severity.value}/{hits[0].certainty.value}")

# --- 3. Metadata bomb: bounded memory --------------------------------------
d = pack("meta_bomb")
write_jar(d / "bomb.jar", {"fabric.mod.json": FABRIC + " " * (400 * MIB)})
check("metadata bomb is small on disk", (d / "bomb.jar").stat().st_size < 2 * MIB)
seen = []
import mmis.minecraft.fabric as fabric_mod
orig_parse = jar_mod.parse_fabric_mod_json
jar_mod.parse_fabric_mod_json = lambda content, *a, **k: (seen.append(len(content)), orig_parse(content, *a, **k))[1]
try:
    r, peak, dt = measured_scan(d.parent)
finally:
    jar_mod.parse_fabric_mod_json = orig_parse
print(f"     400 MiB metadata: peak {peak:.1f} MiB, {dt:.1f} s")
check("metadata bomb: refused with METADATA_TOO_LARGE", codes(r).count(TOO_LARGE) == 1 and not r.mods, f"codes={codes(r)}")
check("metadata bomb: parser never receives oversized content", all(n <= LIMIT for n in seen), f"sizes={seen}")
check("metadata bomb: peak memory under 48 MiB", peak < 48, f"peak={peak:.0f} MiB")
check("metadata bomb: scan under 20 s", dt < 20, f"{dt:.1f}s")

# --- 4. Oversized MANIFEST.MF ----------------------------------------------
d = pack("big_manifest")
toml_jarver = TOML.replace('version="1.0.0"', 'version="${file.jarVersion}"')
manifest = "Manifest-Version: 1.0\r\n" + ("X-Pad: " + "a" * 60 + "\r\n") * (5 * MIB // 69 + 1) + "Implementation-Version: 9.9.9\r\n"
write_jar(d / "m.jar", {"META-INF/MANIFEST.MF": manifest, "META-INF/mods.toml": toml_jarver})
r = scan(str(d.parent))
check("oversized MANIFEST.MF: mod still listed", [m.id for m in r.mods] == ["padded"], f"mods={[m.id for m in r.mods]}")
check("oversized MANIFEST.MF: version not taken from the unread manifest", bool(r.mods) and r.mods[0].version != "9.9.9", f"version={r.mods[0].version if r.mods else None}")
check("oversized MANIFEST.MF: reported", TOO_LARGE in codes(r), f"codes={codes(r)}")

# --- 5. Oversized pack manifests -------------------------------------------
for fname in ("manifest.json", "modrinth.index.json", "mmc-pack.json"):
    d = pack(f"packman_{fname}")
    write_jar(d / "m.jar", {"fabric.mod.json": FABRIC})
    (d.parent / fname).write_text('{"minecraft": {"version": "1.20.1"}}' + " " * LIMIT, encoding="utf-8")
    r, peak, _ = measured_scan(d.parent)
    hits = [f for f in r.findings if f.code == TOO_LARGE]
    check(f"oversized {fname}: refused", len(hits) == 1 and F.PACK_MANIFEST_DETECTED not in codes(r), f"codes={codes(r)}")
    check(f"oversized {fname}: nothing derived from it", r.detected_mc_version is None, f"mc={r.detected_mc_version}")
    check(f"oversized {fname}: jar scan unaffected", [m.id for m in r.mods] == ["padded"])

# --- 6. Large top-level jar is not loaded in memory ---------------------------
d = pack("big_jar")
big = d / "big.jar"
with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
    z.writestr("fabric.mod.json", FABRIC)
    for i in range(30):
        z.writestr(f"assets/blob{i}.bin", os.urandom(10 * MIB))
h = hashlib.sha256()
with open(big, "rb") as fh:
    for chunk in iter(lambda: fh.read(MIB), b""):
        h.update(chunk)
r, peak, dt = measured_scan(d.parent)
print(f"     {big.stat().st_size / MIB:.0f} MiB jar: peak {peak:.1f} MiB, {dt:.1f} s")
check("large jar: scanned", [m.id for m in r.mods] == ["padded"] and not r.findings, f"codes={codes(r)}")
check("large jar: sha256 of the whole file", r.jars[0].sha256 == h.hexdigest())
check("large jar: size_bytes of the whole file", r.jars[0].size_bytes == big.stat().st_size)
check("large jar: peak memory under 32 MiB", peak < 32, f"peak={peak:.0f} MiB")
try:
    os.remove(big)
    released = True
except OSError as exc:
    released = False
    print("    ", exc)
check("large jar: file handle released after scan", released)

# --- 7. Corrupt and degenerate inputs --------------------------------------
d = pack("degenerate")
good = io.BytesIO()
write_jar(good, {"fabric.mod.json": FABRIC, "assets/a.txt": "hello" * 2000})
raw = good.getvalue()
(d / "a_truncated.jar").write_bytes(raw[: len(raw) // 2])
(d / "b_random.jar").write_bytes(os.urandom(4096))
(d / "c_empty.jar").write_bytes(b"")
bad_crc = bytearray(raw)
pos = raw.find(b"assets/a.txt") + len("assets/a.txt") + 4
bad_crc[pos] ^= 0xFF
(d / "d_badcrc.jar").write_bytes(bytes(bad_crc))
write_jar(d / "e_badjson.jar", {"fabric.mod.json": '{"schemaVersion": 1, "id": '})
write_jar(d / "f_badtoml.jar", {"META-INF/mods.toml": "[[mods]\nmodId = "})
write_jar(d / "g_binary_meta.jar", {"fabric.mod.json": os.urandom(2048)})
write_jar(d / "h_wrong_shape.jar", {"fabric.mod.json": "[1, 2, 3]"})
write_jar(d / "i_no_meta.jar", {"assets/a.txt": "x"})
(d / "j_dir.jar").mkdir(exist_ok=True)
try:
    r = scan(str(d.parent))
    crashed = ""
except Exception as exc:  # noqa: BLE001
    r, crashed = None, repr(exc)
check("degenerate pack: scan does not raise", r is not None, crashed)
if r is not None:
    by_jar = {}
    for f in r.findings:
        for e in f.evidence:
            by_jar.setdefault(e.file, set()).add(f.code)
    for name, allowed in [("a_truncated", {F.CORRUPT_JAR, F.NOT_A_ZIP}), ("b_random", {F.NOT_A_ZIP, F.CORRUPT_JAR}), ("c_empty", {F.NOT_A_ZIP, F.CORRUPT_JAR}),
                          ("d_badcrc", {F.CORRUPT_JAR}), ("e_badjson", {F.INVALID_METADATA_JSON}), ("f_badtoml", {F.INVALID_METADATA_TOML}),
                          ("i_no_meta", {F.NO_METADATA})]:
        got = by_jar.get(f"mods/{name}.jar", set())
        check(f"degenerate {name}: controlled finding", bool(got & allowed), f"got={sorted(got)}")
    for name in ("g_binary_meta", "h_wrong_shape"):
        check(f"degenerate {name}: controlled finding", bool(by_jar.get(f"mods/{name}.jar")), "no finding at all")
    check("degenerate pack: no mod invented", not r.mods, f"mods={[m.id for m in r.mods]}")
    check("degenerate pack: directory named *.jar ignored", not any(j.path == "mods/j_dir.jar" for j in r.jars))
    check("degenerate pack: loader stays unknown", r.detected_loader is None and r.detected_mc_version is None)
    r2 = scan(str(d.parent))
    check("degenerate pack: two scans are identical", json.dumps(r.to_dict(), sort_keys=True) == json.dumps(r2.to_dict(), sort_keys=True))

# --- 8. Nested jars ---------------------------------------------------------
inner_over = io.BytesIO()
write_jar(inner_over, {"fabric.mod.json": FABRIC + " " * (LIMIT + 1)})
inner_ok = io.BytesIO()
write_jar(inner_ok, {"fabric.mod.json": FABRIC.replace("padded", "inner_ok")})
d = pack("nested")
write_jar(d / "outer.jar", {
    "fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "outer", "version": "1.0.0"}),
    "META-INF/jars/over.jar": inner_over.getvalue(),
    "META-INF/jars/ok.jar": inner_ok.getvalue(),
})
r = scan(str(d.parent))
check("nested: healthy nested jar still read", sorted(m.id for m in r.mods) == ["inner_ok", "outer"], f"mods={sorted(m.id for m in r.mods)}")
hits = [f for f in r.findings if f.code == TOO_LARGE]
check("nested: oversized nested metadata refused, with nested path",
      len(hits) == 1 and hits[0].evidence[0].file == "mods/outer.jar!/META-INF/jars/over.jar", f"evidence={[e.file for f in hits for e in f.evidence]}")
check("nested: sha256 of nested jar unchanged", any(j.path.endswith("ok.jar") and j.sha256 == hashlib.sha256(inner_ok.getvalue()).hexdigest() for j in r.jars))

# --- 9. Dual-format jar keeps one record per descriptor ------------------------
d = pack("dual")
write_jar(d / "dual.jar", {"fabric.mod.json": FABRIC, "META-INF/mods.toml": TOML})
r = scan(str(d.parent))
check("dual jar: one record per descriptor", [(m.id, [l.value for l in m.loaders]) for m in r.mods] == [("padded", ["fabric"]), ("padded", ["forge"])],
      f"{[(m.id, [l.value for l in m.loaders]) for m in r.mods]}")
jf, mods, _, _ = read_jar((d / "dual.jar").read_bytes(), "mods/dual.jar")
check("dual jar: read_jar from bytes agrees with scan", len(mods) == 2 and jf.mods == ["padded", "padded"], f"mods={jf.mods}")

# --- 10. Well-formed fixture: results unchanged ------------------------------
fixture = work / "fixture_pack"
subprocess.run([sys.executable, str(HERE / "build_pack.py"), str(fixture)], check=True, capture_output=True)
current = json.dumps(scan(str(fixture)).to_dict(), sort_keys=True, indent=1)
# Zip entries carry the build time, so jar hashes differ between fixture builds
current = re.sub(r'"sha256": "[0-9a-f]{64}"', '"sha256": "<build-dependent>"', current)
if not GOLDEN.exists():
    GOLDEN.write_text(current, encoding="utf-8")
    print(f"     golden recorded at {GOLDEN.name} (baseline run)")
else:
    check("fixture pack: scan identical to the pre-hardening golden", current == GOLDEN.read_text(encoding="utf-8"))

print(f"{'FAILED: ' + str(failed) if failed else 'ALL OK'}")
sys.exit(1 if failed else 0)
