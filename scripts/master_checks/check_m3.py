"""Master acceptance check for M3. Usage: check_m3.py <work_dir> (project venv python)."""
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

work = Path(sys.argv[1])
if work.exists():
    shutil.rmtree(work)
mods = work / "pack" / "mods"
mods.mkdir(parents=True)
fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + str(detail) if not cond else ""))
    if not cond:
        fails.append(name)


def jar(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


def fab(mid, ver="1.0.0", extra_files=None, **kw):
    meta = {"schemaVersion": 1, "id": mid, "version": ver}
    meta.update(kw)
    files = {"fabric.mod.json": json.dumps(meta)}
    files.update(extra_files or {})
    return jar(files)


nested = fab("nestedonly", "0.1.0")
J = {"jars": [{"file": "META-INF/jars/nestedonly.jar"}]}
N = {"META-INF/jars/nestedonly.jar": nested}
(work / "pack" / "manifest.json").write_text(json.dumps({
    "manifestType": "minecraftModpack", "manifestVersion": 1, "name": "T", "version": "1",
    "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-0.15.11", "primary": True}]}, "files": []}))
w = lambda name, data: (mods / name).write_bytes(data)
w("core.jar", fab("core", "2.0.0", N, provides=["core_api"], **J))
w("a.jar", fab("a", "1.0.0", N, depends={"core": "^2.0.0", "missinglib": ">=1", "core_api": "*", "fabricloader": ">=0.15", "java": ">=17", "minecraft": "1.20.1"}, **J))
w("b.jar", fab("b", depends={"core": ">=3.0.0"}, recommends={"optmissing": "*"}, suggests={"core": ">=5"}))
w("c.jar", fab("c", depends={"d": "*"}))
w("d.jar", fab("d", depends={"e": "*"}))
w("e.jar", fab("e", depends={"c": "*"}))
w("weird.jar", fab("weird", "release-final"))
w("f.jar", fab("f", depends={"weird": ">=1.0"}))
w("g.jar", fab("g", depends={"offmod": "*"}))
w("offmod.jar.disabled", fab("offmod"))
w("h.jar", fab("h", depends={"nestedonly": ">=0.1.0"}))
w("dup-1.0.jar", fab("dup", "1.0"))
w("dup-1.1.jar", fab("dup", "1.1"))
w("brk.jar", fab("brk", breaks={"core": "<3.0.0", "b": ">=5"}, conflicts={"a": "*"}))
w("forgemod.jar", jar({"META-INF/mods.toml": 'modLoader="javafml"\nloaderVersion="[47,)"\n[[mods]]\nmodId="forgemod"\nversion="1.0"\n[[dependencies.forgemod]]\nmodId="forge"\nmandatory=true\nversionRange="[47,)"\n'}))
w("multi.jar", jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "multi", "version": "1.0"}),
                    "META-INF/mods.toml": 'modLoader="javafml"\nloaderVersion="[47,)"\n[[mods]]\nmodId="multi"\nversion="1.0"\n[[dependencies.multi]]\nmodId="forge"\nmandatory=true\nversionRange="[47,)"\n'}))
w("future.jar", fab("future", depends={"minecraft": ">=1.21"}))
w("prov2.jar", fab("prov2", provides=["core_api"]))
w("garbage.jar", jar({"readme.txt": "x"}))

outs = set()
for seed in ("0", "5", "42"):
    p = subprocess.run([sys.executable, "-m", "mmis", "analyze", str(work / "pack"), "--json"], capture_output=True, text=True, env=dict(os.environ, PYTHONHASHSEED=seed))
    outs.add(p.stdout)
check("exit0", p.returncode == 0, (p.returncode, p.stderr[-400:]))
check("deterministic", len(outs) == 1)
d = json.loads(outs.pop())


def find_all(obj):
    if isinstance(obj, dict):
        if "code" in obj and "severity" in obj and "evidence" in obj:
            yield obj
        for v in obj.values():
            yield from find_all(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_all(v)


F = list(find_all(d))
print("top keys:", sorted(d))
for f in sorted(F, key=lambda f: (f["code"], f["related_mod_ids"])):
    print(f"  {f['code']:34} {f['severity']:8} {f['certainty']:18} {f['related_mod_ids']} | {f['message'][:110]}")


def has(code, *mods_, certainty=None, severity=None):
    return [f for f in F if f["code"] == code and set(mods_) <= set(f["related_mod_ids"])
            and (certainty is None or f["certainty"] == certainty) and (severity is None or f["severity"] == severity)]


check("missing-required", len(has("DEP_MISSING_REQUIRED", "a", "missinglib", certainty="certain", severity="error")) == 1)
check("only-one-missing-required", len(has("DEP_MISSING_REQUIRED")) == 1, [f["related_mod_ids"] for f in has("DEP_MISSING_REQUIRED")])
check("version-mismatch", len(has("DEP_VERSION_MISMATCH", "b", "core", certainty="certain")) == 1)
check("only-one-mismatch", len(has("DEP_VERSION_MISMATCH")) == 1, [f["related_mod_ids"] for f in has("DEP_VERSION_MISMATCH")])
check("missing-optional", len(has("DEP_MISSING_OPTIONAL", "b", "optmissing")) == 1 and len(has("DEP_MISSING_OPTIONAL")) == 1)
check("optional-mismatch", len(has("DEP_OPTIONAL_MISMATCH", "b", "core", severity="warning")) == 1)
check("undetermined", len(has("DEP_VERSION_UNDETERMINED", "f", "weird", certainty="insufficient_data")) == 1)
check("disabled-provider", len(has("DEP_DISABLED_PROVIDER", "g", "offmod")) == 1)
cyc = has("DEP_CYCLE")
check("one-cycle", len(cyc) == 1 and set(cyc[0]["related_mod_ids"]) == {"c", "d", "e"}, [f["related_mod_ids"] for f in cyc])
check("dup", len(has("CONFLICT_DUPLICATE_MOD", "dup", certainty="certain")) == 1 and len(has("CONFLICT_DUPLICATE_MOD")) == 1, [f["related_mod_ids"] for f in has("CONFLICT_DUPLICATE_MOD")])
check("breaks", len(has("CONFLICT_BREAKS_DECLARED", "brk", "core", certainty="certain")) == 1 and len(has("CONFLICT_BREAKS_DECLARED")) == 1)
check("soft", len(has("CONFLICT_SOFT_DISCOURAGED", "brk", "a", certainty="possible")) == 1)
lm = has("CONFLICT_LOADER_MISMATCH")
check("loader-mismatch", len(lm) == 1 and "forgemod" in lm[0]["related_mod_ids"] and lm[0]["certainty"] == "certain", [f["related_mod_ids"] for f in lm])
mc = has("CONFLICT_MC_VERSION_MISMATCH")
check("mc-mismatch", len(mc) == 1 and "future" in mc[0]["related_mod_ids"], [f["related_mod_ids"] for f in mc])
check("multi-providers", len(has("CONFLICT_MULTIPLE_PROVIDERS", certainty="possible")) == 1, [f["related_mod_ids"] for f in has("CONFLICT_MULTIPLE_PROVIDERS")])
un = has("CONFLICT_UNANALYZABLE_JARS")
check("unanalyzable", len(un) == 1 and un[0]["certainty"] == "insufficient_data" and "garbage.jar" in json.dumps(un[0]))
check("platform-not-missing", not [f for f in F if f["code"].startswith("DEP_MISSING") and set(f["related_mod_ids"]) & {"fabricloader", "java", "minecraft", "forge"}])
check("multi-clean", not [f for f in F if "multi" in f["related_mod_ids"]], [(f["code"], f["related_mod_ids"]) for f in F if "multi" in f["related_mod_ids"]])
check("nested-clean", not [f for f in F if "nestedonly" in f["related_mod_ids"]], [(f["code"], f["related_mod_ids"]) for f in F if "nestedonly" in f["related_mod_ids"]])
ana = [f for f in F if f["code"].startswith(("DEP_", "CONFLICT_", "PLATFORM_"))]
check("evidence+suggestion", all(f["evidence"] and f.get("suggestion") for f in ana), [f["code"] for f in ana if not (f["evidence"] and f.get("suggestion"))])
p = subprocess.run([sys.executable, "-m", "mmis", "analyze", str(work / "pack")], capture_output=True, text=True)
check("human", p.returncode == 0 and "missinglib" in p.stdout and "Traceback" not in p.stderr, p.stderr[-300:])
print("---- human output head ----")
print(p.stdout[:1800])
shutil.rmtree(work)
print("FAILED:", fails)
sys.exit(1 if fails else 0)
