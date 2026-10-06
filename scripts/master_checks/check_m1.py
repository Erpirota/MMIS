"""Master acceptance check for M1. Usage: check_m1.py <pack_dir> (run with the project venv python)."""
import json
import os
import subprocess
import sys

pack = sys.argv[1]
fails = []


def check(name, cond, detail=""):
    if not cond:
        fails.append(f"{name} {detail}")


outs = []
for seed in ("0", "1", "123", "999"):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    p = subprocess.run([sys.executable, "-m", "mmis", "scan", pack, "--json"], capture_output=True, env=env)
    check("exit0", p.returncode == 0, str(p.returncode))
    outs.append(p.stdout)
check("deterministic", len(set(outs)) == 1)
d = json.loads(outs[0])
check("no-abs-path", pack.replace("\\", "/").lower() not in outs[0].decode().replace("\\\\", "/").lower())
mods = {m["id"]: m for m in d["mods"]}
deps = lambda mid: {x["mod_id"]: x for x in mods[mid]["dependencies"]}

check("mods", set(mods) == {"alpha", "nestedlib", "lenient", "forgeone", "forgetwo", "noversion", "neo", "quilty", "off"}, str(sorted(mods)))
check("loader-none", d["detected_loader"] is None)
q = mods["quilty"]
check("quilt-name", q["name"] == "Quilty", repr(q["name"]))
qd = deps("quilty")
check("quilt-breaks", qd.get("qbad", {}).get("kind") == "breaks", str(sorted(qd)))
check("quilt-nover", qd["simple_dep"]["version_range"] is None and qd["qopt"]["version_range"] is None)
check("quilt-opt", qd["qopt"]["kind"] == "optional" and qd["qdep"]["version_range"]["raw"] == ">=1.0.0")
check("quilt-star", qd.get("qbad", {}).get("version_range", {}) and qd["qbad"]["version_range"]["raw"] == "*")
nd = deps("neo")
check("neo-nover", nd["meh2"]["version_range"] is None)
check("neo-kinds", (nd["other"]["kind"], nd["meh2"]["kind"], nd["req"]["kind"]) == ("breaks", "conflicts", "required"))
a = mods["alpha"]
check("alpha", a["version"] == "1.2.3" and a["provides"] == ["alpha_api"] and a["authors"] == ["Ann", "Bob"])
check("alpha-related", a["related_files"] == ["config/alpha-client.toml", "config/alpha/main.json"], str(a["related_files"]))
check("forge-placeholder", mods["forgeone"]["version"] == "4.5.6" and mods["noversion"]["version"] is None)
check("lenient", mods["lenient"]["name"] is None and mods["lenient"]["version"] is None)
check("nested", mods["nestedlib"]["parent_jar"] == "mods/alpha-1.2.3.jar")
codes = sorted(f["code"] for f in d["findings"])
for c in ("AMBIGUOUS_LOADER", "INVALID_METADATA_JSON", "INVALID_METADATA_TOML", "LEGACY_MOD_INFO", "NOT_A_ZIP", "NO_METADATA", "UNRESOLVED_PLACEHOLDER"):
    check("finding-" + c, c in codes, str(codes))
check("evidence", all(f["evidence"] for f in d["findings"]))
check("jars", len(d["jars"]) == 13 and sum(j["is_disabled"] for j in d["jars"]) == 1)

from mmis.domain.versions import VersionRange  # noqa: E402

T, F, N = True, False, None
cases = [
    ("^0.2.3", "fabric", "0.9.0", T), ("^0.0.3", "fabric", "0.0.4", T), ("^1.2.3", "fabric", "1.2.2", F), ("^1.2.3", "fabric", "2.0.0", F),
    ("~1.2.3", "fabric", "1.2.99", T), ("~1.2.3", "fabric", "1.3.0", F), (">=1.20", "fabric", "1.20.0", T), ("1.20", "fabric", "1.20.0", T),
    (">=1.20.1", "fabric", "1.20", F), ("<1.21", "fabric", "1.20.6", T), (">=1.0.0", "fabric", "1.0.0-beta.1", F), ("<1.0.0", "fabric", "1.0.0-beta.1", T),
    (">=1.0.0-alpha", "fabric", "1.0.0-beta", T), (">=1.0.0-beta.2", "fabric", "1.0.0-beta.10", T), (">=1.0.0-rc.1", "fabric", "1.0.0", T),
    ("=1.2.3", "fabric", "1.2.3+build.5", T), (">=0.92.0", "fabric", "0.92.2+1.20.1", T), (">=0.92.0+1.20.1", "fabric", "0.91.0+1.20.1", F),
    ("*", "fabric", "24w14a", T), ("1.20.x", "fabric", "1.20", T), ("1.x", "fabric", "1.0.0", T), (">=1.20", "fabric", "24w14a", N),
    (">=1.20", "fabric", "", N), ("garbage!!", "fabric", "1.0.0", N), (">=1.20.1  <1.21", "fabric", "1.20.4", T), (">=1.20.1 <1.21", "fabric", "1.21", F),
    ("[1.20.1]", "maven", "1.20.1", T), ("[1.20.1]", "maven", "1.20.2", F), ("[1.20,1.21)", "maven", "1.20.6", T), ("[1.20,1.21)", "maven", "1.21", F),
    ("[1.20,1.21)", "maven", "1.21.0", F), ("(1.0,2.0]", "maven", "1.0", F), ("(1.0,2.0]", "maven", "2.0", T), ("[47,)", "maven", "47.2.0", T),
    ("[47,)", "maven", "46.9", F), ("(,1.5]", "maven", "1.5", T), ("(,1.5)", "maven", "1.5", F), ("[1.0,2.0),[3.0,)", "maven", "2.5", F),
    ("[1.0,2.0),[3.0,)", "maven", "3.1", T), ("[1.0,2.0),[3.0,)", "maven", "1.5", T), ("*", "maven", "1.0", T),
    ("1.0", "maven", "0.5", N), ("1.0", "maven", "1.0", T), ("[1.20.1,)", "maven", "1.20.1-47.2.0", T), ("[11.0.0.0,)", "maven", "11.0.1.3", T),
    ("[1.0", "maven", "1.0", N), ("[2.0,1.0]", "maven", "1.5", N), ("[1.19.2,1.20)", "maven", "1.19.4", T), ("[0.5.1,)", "maven", "0.5.1.f", T),
    ("[1.0,)", "maven", "1.0-SNAPSHOT", F), ("[1.0,)", "maven", "1.0.0", T), ("[1.0-rc1,)", "maven", "1.0-beta2", F), ("[1.0-beta2,)", "maven", "1.0-rc1", T),
    ("(,1.0)", "maven", "1.0-alpha", T), ("[1.20.1-47.1.0,)", "maven", "1.20.1-47.2.0", T), ("[1.20.1-47.3.0,)", "maven", "1.20.1-47.2.0", F),
]
for raw, syn, v, exp in cases:
    got = VersionRange.parse(raw, syn).matches(v)
    check("ver", got is exp, f"{syn} {raw!r} vs {v!r}: expected {exp} got {got}")
check("invalid-range", VersionRange.parse("[2.0,1.0]", "maven").is_valid is False)
for raw, syn, exp in [("1.20.1", "fabric", "1.20.1"), ("[1.20.1]", "maven", "1.20.1"), ("[1.20.1,1.20.1]", "maven", "1.20.1"), (">=1.20.1", "fabric", None), ("1.20.1", "maven", None)]:
    got = VersionRange.parse(raw, syn).extract_exact_version()
    check("exact", got == exp, f"{syn} {raw!r}: expected {exp} got {got}")

print(f"checks failed: {len(fails)}")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
