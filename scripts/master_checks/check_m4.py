"""Master acceptance check for M4. Usage: check_m4.py <scratch_dir> (project venv python)."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sp = Path(sys.argv[1])
fails = []
ghost = Path(tempfile.gettempdir()) / "mmis-master-ghost-datadir"
ENV = dict(os.environ, MMIS_DATA_DIR=str(ghost))


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + str(detail) if not cond else ""))
    if not cond:
        fails.append(name)


def analyze(path, seed="0"):
    p = subprocess.run([sys.executable, "-m", "mmis", "crash", "analyze", str(path), "--json"], capture_output=True, text=True, env=dict(ENV, PYTHONHASHSEED=seed))
    return p.returncode, p.stdout, p.stderr


def load(path):
    rc, out, err = analyze(path)
    check(f"exit0 {Path(path).name}", rc == 0, (rc, err[-300:]))
    d = json.loads(out)
    return d.get("result", d)


def codes(r):
    return [f["code"] for f in r["findings"]]


PLATFORM = {"fabric", "fabricloader", "fabric-loader", "minecraft", "forge", "neoforge", "java", "fml", None}

# --- Forge crash report
r = load(sp / "crash_forge.txt")
s = r["suspects"]
print("forge suspects:", [(x["mod_id"], x["attribution"], x["certainty"], x["rank"]) for x in s])
check("forge-kind", r["kind"] == "crash_report" and r["mc_version"] == "1.20.1")
check("forge-root", r["root_cause"]["exception_class"] == "java.lang.NullPointerException")
check("forge-chain", [e_["exception_class"] for e_ in (lambda e: [e, e["caused_by"], e["caused_by"]["caused_by"]])(r["exceptions"][0])] == ["java.lang.RuntimeException", "java.lang.IllegalStateException", "java.lang.NullPointerException"])
f0 = r["exceptions"][0]["frames"][0]
check("forge-jar-clean", f0["jar_file"] == "shinyblocks-2.4.1.jar", f0["jar_file"])
check("forge-no-fake-mixin", "classloading" not in json.dumps(f0.get("mixin_info")), f0.get("mixin_info"))
check("forge-mixin-frame", "fastrender.mixins.json" in json.dumps(r["exceptions"][0]["frames"][1]))
check("forge-top-suspect", s and s[0]["mod_id"] == "shinyblocks" and s[0]["attribution"] == "frame_jar" and s[0]["certainty"] == "possible")
check("forge-mixin-suspect", any(x["mod_id"] == "fastrender" and x["attribution"] == "mixin" for x in s))
check("forge-suspects-unique", len({x["mod_id"] for x in s}) == len(s))
check("forge-no-platform-suspect", not [x for x in s if x["mod_id"] in PLATFORM])
check("forge-not-certain", all(x["certainty"] != "certain" for x in s))
check("forge-modlist", {m.get("id") or m.get("mod_id") for m in r["mods_listed"]} >= {"shinyblocks", "fastrender", "forge"})
check("forge-loader", (r.get("detected_loader") or r.get("loader")) == "forge", r.get("detected_loader"))

# --- Fabric dependency resolution failure
r = load(sp / "crash_fabric_dep.txt")
s = r["suspects"]
print("fabric suspects:", [(x["mod_id"], x["attribution"], x["certainty"]) for x in s])
for f in r["findings"]:
    print("  ", f["code"], f["certainty"], f["related_mod_ids"], "|", f["message"][:100], "| S:", (f.get("suggestion") or "")[:80])
ex = [f for f in r["findings"] if f["code"] == "CRASH_LOADER_EXPLICIT_ISSUE"]
check("fabric-explicit", len(ex) >= 1 and ex[0]["certainty"] == "certain" and set(ex[0]["related_mod_ids"]) == {"lithium", "fabric-api"}, [(f["code"], f["related_mod_ids"]) for f in r["findings"]])
check("fabric-suggestion", ex and "fabric-api" in (ex[0].get("suggestion") or ""))
check("fabric-suspect", s and s[0]["mod_id"] == "lithium" and s[0]["attribution"] == "loader_statement" and s[0]["certainty"] == "certain")
check("fabric-no-platform", not [x for x in s if x["mod_id"] in PLATFORM], [x["mod_id"] for x in s])
check("fabric-versions", r["mc_version"] == "1.20.1" and (r.get("detected_loader") or r.get("loader")) == "fabric", (r["mc_version"], r.get("detected_loader")))
check("fabric-related-no-platform", not [f for f in r["findings"] if set(f["related_mod_ids"]) & {"fabric", "fabricloader", "fabric-loader"}])

# --- Forge missing dependency block inside a log
forge_dep = sp / "crash_forge_dep.txt"
forge_dep.write_text(
    "[12:00:01] [main/INFO] [ne.mi.fm.lo.mo.ModDiscoverer/SCAN]: Found mod file examplemod-1.0.jar of type MOD\n"
    "[12:00:02] [main/ERROR] [ne.mi.fm.lo.ModSorter/LOADING]: Missing or unsupported mandatory dependencies:\n"
    "\tMod ID: 'geckolib', Requested by: 'examplemod', Expected range: '[4.2,)', Actual version: '[MISSING]'\n"
    "\tMod ID: 'curios', Requested by: 'othermod', Expected range: '[5.4,)', Actual version: '5.1.0'\n"
    "[12:00:02] [main/INFO] [minecraft/Main]: done\n", encoding="utf-8")
r = load(forge_dep)
ex = [f for f in r["findings"] if f["code"] == "CRASH_LOADER_EXPLICIT_ISSUE"]
rel = [set(f["related_mod_ids"]) for f in ex]
print("forge-dep findings:", [(f["code"], f["related_mod_ids"], f["message"][:90]) for f in r["findings"]])
check("forgedep-both", {"geckolib", "examplemod"} in rel and {"curios", "othermod"} in rel and all(f["certainty"] == "certain" for f in ex), rel)
check("forgedep-distinguish", any("5.1.0" in f["message"] for f in ex))
forge_dep.unlink()

# --- No exception
r = load(sp / "crash_none.txt")
check("none", codes(r) == ["CRASH_NO_EXCEPTION_FOUND"] and not r["suspects"], (codes(r), r["suspects"]))

# --- hostile inputs
for name, data in {"empty.txt": b"", "binary.bin": bytes(range(256)) * 400, "crlf.txt": (sp / "crash_forge.txt").read_bytes().replace(b"\n", b"\r\n")}.items():
    p = sp / ("tmp_" + name)
    p.write_bytes(data)
    rc, out, err = analyze(p)
    check("hostile-" + name, rc == 0 and "Traceback" not in err, (rc, err[-200:]))
    if name == "crlf.txt" and rc == 0:
        rr = json.loads(out)
        rr = rr.get("result", rr)
        check("crlf-same-suspect", rr["suspects"] and rr["suspects"][0]["mod_id"] == "shinyblocks")
    p.unlink()
rc, out, err = analyze(sp / "does_not_exist.txt")
check("missing-file", rc in (1, 2) and "Traceback" not in err, (rc, err[-200:]))

# --- performance: ~20 MB log with an exception at the end
big = sp / "tmp_big.log"
line = "[10:00:00] [Server thread/INFO]: Player moved wrongly! some filler text to make the line realistically long ({}).\n"
with open(big, "w", encoding="utf-8") as fh:
    n = 0
    i = 0
    while n < 19 * 1024 * 1024:
        s_ = line.format(i)
        fh.write(s_)
        n += len(s_)
        i += 1
    fh.write((sp / "crash_fabric_dep.txt").read_text(encoding="utf-8"))
t = time.perf_counter()
rc, out, err = analyze(big)
dt = time.perf_counter() - t
print(f"big log: {big.stat().st_size / 1e6:.1f} MB in {dt:.1f}s exit {rc}")
check("big-time", rc == 0 and dt < 30, dt)
if rc == 0:
    rr = json.loads(out)
    rr = rr.get("result", rr)
    check("big-found", "CRASH_LOADER_EXPLICIT_ISSUE" in codes(rr), codes(rr))
big.unlink()

outs = {analyze(sp / "crash_forge.txt", seed=s_)[1] for s_ in ("0", "3", "99")}
check("deterministic", len(outs) == 1)
check("no-datadir-side-effect", not ghost.exists())
for args in (["--help"], ["scan", "--help"], ["profile", "--help"], ["crash", "analyze", "--help"]):
    p = subprocess.run([sys.executable, "-m", "mmis"] + args, capture_output=True, text=True, env=ENV)
    check("help " + " ".join(args), p.returncode == 0 and "usage" in p.stdout.lower(), (p.returncode, p.stderr[-100:]))
check("no-datadir-after-help", not ghost.exists())
print("FAILED:", fails)
sys.exit(1 if fails else 0)
