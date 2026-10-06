"""Master acceptance check for M2. Usage: check_m2.py <source_pack> <work_dir> (run with project venv python)."""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

src, work = Path(sys.argv[1]), Path(sys.argv[2])
if work.exists():
    shutil.rmtree(work)
pack = work / "pack"
data = work / "data"
shutil.copytree(src, pack)
fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + str(detail) if not cond else ""))
    if not cond:
        fails.append(name)


def run(*args, seed="0", env_extra=None, use_flag=True):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    env.pop("MMIS_DATA_DIR", None)
    env.update(env_extra or {})
    cmd = [sys.executable, "-m", "mmis"] + (["--data-dir", str(data)] if use_flag else []) + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def jar(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return buf.getvalue()


before = {p.relative_to(pack).as_posix(): p.stat().st_mtime_ns for p in pack.rglob("*")}

rc, out, err = run("profile", "add", "Main Pack", str(pack), "--json")
check("add", rc == 0, (rc, err[-300:]))
rc, out, err = run("profile", "add", "main pack", str(pack))
check("dup-name-ci", rc != 0 and "Traceback" not in err, (rc, err[-200:]))
rc, out, err = run("profile", "list", "--json")
lst = json.loads(out) if rc == 0 else None
check("list-never-scanned", rc == 0 and "never_scanned" in out, out[:300])
rc, out, err = run("profile", "scan", "Main Pack", "--label", "first", "--json")
check("scan1", rc == 0, (rc, err[-300:]))
rc, out, err = run("profile", "diff", "Main Pack")
check("diff-needs-two", rc == 1 and "Traceback" not in err, (rc, err[-200:]))

# mutate pack: version bump, removal, addition, enable disabled, sha-only change
(pack / "mods" / "neo.jar").unlink()
(pack / "mods" / "alpha-1.2.3.jar").unlink()
(pack / "mods" / "alpha-1.3.0.jar").write_bytes(jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "alpha", "version": "1.3.0", "name": "Alpha Mod"})}))
(pack / "mods" / "newmod.jar").write_bytes(jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "newmod", "version": "9.9.9"})}))
(pack / "mods" / "off.jar.disabled").rename(pack / "mods" / "off.jar")
(pack / "mods" / "lenient.jar").write_bytes(jar({"fabric.mod.json": '{"schemaVersion":1,"id":"lenient"}', "extra.txt": "changed"}))
(pack / "mods" / "corrupt.jar").unlink()

rc, out, err = run("profile", "scan", "Main Pack", "--json")
check("scan2", rc == 0, (rc, err[-300:]))
rc, hist, err = run("profile", "history", "Main Pack", "--json")
check("history", rc == 0 and hist.count("scanned_at") == 2 and "first" in hist, hist[:400])
outs = {run("profile", "diff", "Main Pack", "--json", seed=s)[1] for s in ("0", "1", "77")}
check("diff-deterministic", len(outs) == 1)
diff_text = outs.pop()
print("---- DIFF JSON (for Master reading) ----")
print(diff_text[:6000])
print("----")
for token in ("newmod", "neo", "alpha", "1.3.0", "1.2.3", "off", "lenient", "nestedlib", "NOT_A_ZIP"):
    check("diff-mentions-" + token, token in diff_text)
rc, out, err = run("profile", "diff", "Main Pack")
check("diff-human", rc == 0 and "newmod" in out, (rc, out[:300], err[-200:]))

rc, out, err = run("profile", "list", "--json", use_flag=False, env_extra={"MMIS_DATA_DIR": str(data)})
check("env-data-dir", rc == 0 and "Main Pack" in out, (rc, out[:200], err[-200:]))
rc, out, err = run("profile", "scan", "Nope")
check("notfound-exit2", rc == 2 and "Traceback" not in err, (rc, err[-200:]))
rc, out, err = run("profile", "diff", "Main Pack", "9998", "9999")
check("scan-notfound", rc == 2 and "Traceback" not in err, (rc, err[-200:]))

after = {p.relative_to(pack).as_posix() for p in pack.rglob("*")}
expected_names = (set(before) - {"mods/neo.jar", "mods/alpha-1.2.3.jar", "mods/off.jar.disabled", "mods/corrupt.jar"}) | {"mods/alpha-1.3.0.jar", "mods/newmod.jar", "mods/off.jar"}
check("pack-untouched", after == expected_names, sorted(after ^ expected_names))

db = data / "mmis.db"
check("db-file", db.exists(), list(data.glob("*")) if data.exists() else "no data dir")
if db.exists():
    con = sqlite3.connect(db)
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    tables = sorted(r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"))
    nscans = con.execute("select count(*) from scans").fetchone()[0]
    print("schema version", ver, "tables", tables, "scans", nscans)
    check("schema", ver >= 1 and {"profiles", "scans"} <= set(tables) and nscans == 2, (ver, tables, nscans))
    con.execute(f"PRAGMA user_version = {ver + 50}")
    con.commit()
    con.close()
    size = db.stat().st_size
    rc, out, err = run("profile", "list")
    check("schema-too-new", rc == 1 and "Traceback" not in err and db.stat().st_size == size, (rc, err[-300:]))
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {ver}")
    con.commit()
    con.close()

shutil.rmtree(pack)
rc, out, err = run("profile", "list", "--json")
check("status-path-missing", rc == 0 and "path_missing" in out, out[:300])
rc, out, err = run("profile", "scan", "Main Pack")
check("scan-missing-path-exit1", rc == 1 and "Traceback" not in err, (rc, err[-200:]))
rc, hist2, err = run("profile", "history", "Main Pack", "--json")
check("no-row-on-missing", hist2.count("scanned_at") == 2)
rc, out, err = run("profile", "remove", "Main Pack")
check("remove", rc == 0)
if db.exists():
    con = sqlite3.connect(db)
    check("cascade", con.execute("select count(*) from scans").fetchone()[0] == 0)
    con.close()
(data / "bad").mkdir(parents=True, exist_ok=True)
(data / "bad" / "mmis.db").write_bytes(b"not a database at all" * 50)
p = subprocess.run([sys.executable, "-m", "mmis", "--data-dir", str(data / "bad"), "profile", "list"], capture_output=True, text=True)
check("not-sqlite", p.returncode == 1 and "Traceback" not in p.stderr, (p.returncode, p.stderr[-300:]))

shutil.rmtree(work)
print("FAILED:", fails)
sys.exit(1 if fails else 0)
