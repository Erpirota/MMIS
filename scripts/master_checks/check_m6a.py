"""Master acceptance check for M6 part A. Usage: check_m6a.py <work_dir> (project venv python, cwd = repo)."""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

work = Path(sys.argv[1])
if work.exists():
    shutil.rmtree(work)
data, pack, out = work / "data", work / "pack", work / "out"
(pack / "mods").mkdir(parents=True)
out.mkdir(parents=True)
fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + str(detail)[:400] if not cond else ""))
    if not cond:
        fails.append(name)


def run(*args, d=None, seed="0"):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    env.pop("MMIS_DATA_DIR", None)
    p = subprocess.run([sys.executable, "-m", "mmis", "--data-dir", str(d or data)] + [str(a) for a in args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, p.stdout, p.stderr


def jar(meta):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fabric.mod.json", json.dumps(meta))
    return buf.getvalue()


XSS1, XSS2, XSS3 = "<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "</style><b>pwn</b>"
(pack / "manifest.json").write_text(json.dumps({"manifestType": "minecraftModpack", "manifestVersion": 1, "name": "T", "version": "1", "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-0.15.11", "primary": True}]}, "files": []}))
(pack / "mods" / "evil.jar").write_bytes(jar({"schemaVersion": 1, "id": "evil", "version": "1.0", "name": XSS1, "description": XSS3, "authors": [XSS2], "depends": {XSS2: ">=1"}}))
(pack / "mods" / "good.jar").write_bytes(jar({"schemaVersion": 1, "id": "good", "version": "2.0", "name": "Good", "depends": {"missing_lib": ">=1"}}))
(pack / "mods" / ("we'rd&amp.jar")).write_bytes(jar({"schemaVersion": 1, "id": "weird", "version": "1"}))

PROFILE = "P <b>1</b> & co"
check("add", run("profile", "add", PROFILE, pack)[0] == 0)
check("scan", run("profile", "scan", PROFILE)[0] == 0)

# ---- report json
rj = out / "r.json"
rc, o, e = run("report", PROFILE, "--format", "json", "--out", rj)
check("report-json", rc == 0 and rj.exists(), (rc, e[-300:]))
b1 = rj.read_bytes() if rj.exists() else b""
rc, o, e = run("report", PROFILE, "--format", "json", "--out", rj)
check("no-overwrite", rc == 1 and rj.read_bytes() == b1 and "Traceback" not in e, (rc, e[-200:]))
rc, o, e = run("report", PROFILE, "--format", "json", "--out", rj, "--force", seed="77")
check("force+deterministic", rc == 0 and rj.read_bytes() == b1, rc)
rep = json.loads(b1) if b1 else {}
print("report keys:", sorted(rep))
check("schema-version", rep.get("schema_version") == 1)
txt = json.dumps(rep)
check("report-has-finding", "DEP_MISSING_REQUIRED" in txt and "missing_lib" in txt)
check("report-recommendations", bool(rep.get("recommendations")))
check("labels", "Certain" in txt)

# ---- report html
rh = out / "r.html"
rc, o, e = run("report", PROFILE, "--format", "html", "--out", rh)
check("report-html", rc == 0 and rh.exists(), (rc, e[-300:]))
html = rh.read_text(encoding="utf-8") if rh.exists() else ""
low = html.lower()
check("html-no-script", "<script" not in low)
check("html-no-xss", XSS1 not in html and "<img src=x" not in low and "onerror=alert(1)>" not in html.replace("&gt;", "") and "</style><b>pwn" not in low and "<b>1</b>" not in html and "we'rd&amp.jar" not in html)
check("html-escaped-present", "&lt;script&gt;alert(1)&lt;/script&gt;" in html)
check("html-style-count", low.count("</style>") == low.count("<style"), (low.count("<style"), low.count("</style>")))
check("html-no-external", "http://" not in low and "https://" not in low and "url(" not in low)
check("html-csp", "content-security-policy" in low and "default-src 'none'" in low)
check("html-content", "missing_lib" in html and "DEP_MISSING_REQUIRED" in html and "Possible" in html or "Certain" in html)


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad, self.tags = [], [], set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for k, v in attrs:
            if k.startswith("on") or (v and "javascript:" in v.lower()):
                self.bad.append((tag, k))
        if tag not in ("meta", "br", "hr", "link", "img", "input", "col"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.bad.append(("mismatch", tag))


p = P()
p.feed(html)
check("html-wellformed", not p.bad and not p.stack, (p.bad[:5], p.stack[:5]))
check("html-no-active-tags", not (p.tags & {"script", "img", "iframe", "object", "embed", "link", "form", "b", "i"}), p.tags & {"script", "img", "iframe", "object", "embed", "link", "form", "b", "i"})
print("html size", len(html), "tags", sorted(p.tags))

# ---- snapshot export / diff against file
rc, hist, e = run("profile", "history", PROFILE, "--json")
sid = None
try:
    h = json.loads(hist)
    items = h if isinstance(h, list) else next(v for v in h.values() if isinstance(v, list))
    sid = items[0].get("id") or items[0].get("scan_id")
except Exception as exc:  # noqa: BLE001
    print("history parse problem", exc, hist[:200])
snap = out / "snap.json"
rc, o, e = run("profile", "export-scan", PROFILE, sid, "--out", snap)
check("export-scan", rc == 0 and snap.exists(), (rc, e[-300:]))
(pack / "mods" / "good.jar").unlink()
run("profile", "scan", PROFILE)
rc, hist, e = run("profile", "history", PROFILE, "--json")
h = json.loads(hist)
items = h if isinstance(h, list) else next(v for v in h.values() if isinstance(v, list))
sid2 = items[0].get("id") or items[0].get("scan_id")
rc, o, e = run("profile", "diff", PROFILE, sid2, "--file", snap, "--json")
check("diff-file", rc == 0 and "good" in o, (rc, o[:200], e[-300:]))
bad = out / "bad.json"
bad.write_text('{"nope": 1}')
rc, o, e = run("profile", "diff", PROFILE, sid2, "--file", bad)
check("snapshot-invalid", rc == 1 and "Traceback" not in e, (rc, e[-200:]))

# ---- backup
bk = out / "b.zip"
rc, o, e = run("backup", "create", "--out", bk)
check("backup-create", rc == 0 and bk.exists(), (rc, e[-300:]))
names = sorted(zipfile.ZipFile(bk).namelist()) if bk.exists() else []
check("backup-members", set(names) <= {"manifest.json", "mmis.db", "settings.json"} and {"manifest.json", "mmis.db"} <= set(names), names)
check("backup-verify", run("backup", "verify", bk)[0] == 0)
rc, o, e = run("backup", "restore", bk)
check("restore-needs-yes", rc == 1 and "Traceback" not in e, (rc, e[-200:]))
d2 = work / "data2"
rc, o, e = run("backup", "restore", bk, "--yes", d=d2)
check("restore-empty", rc == 0, (rc, e[-300:]))
rc, o, e = run("profile", "list", "--json", d=d2)
check("restored-data", rc == 0 and "missing_lib" not in o and "P <b>1" in json.dumps(json.loads(o)), o[:200])
rc, o, e = run("profile", "history", PROFILE, "--json", d=d2)
check("restored-scans", rc == 0 and o.count("scanned_at") == 2, o[:200])


def state(d):
    return {p_.name: hashlib.sha256(p_.read_bytes()).hexdigest() for p_ in sorted(Path(d).iterdir()) if p_.is_file()}


def tamper(name, fn):
    t = out / f"t_{name}.zip"
    src = zipfile.ZipFile(bk)
    members = {n: src.read(n) for n in src.namelist()}
    with zipfile.ZipFile(t, "w") as z:
        fn(z, members)
    before = state(data)
    rc, o, e = run("backup", "restore", t, "--yes")
    after = state(data)
    leftovers = [p_.name for p_ in Path(data).iterdir() if p_.name not in before and "safety" not in p_.name.lower() and not p_.name.endswith(".log")]
    check(f"refuse-{name}", rc == 1 and "Traceback" not in e and before == {k: v for k, v in after.items() if k in before} and not leftovers, (rc, (o + e)[-250:], leftovers))
    rc2 = run("backup", "verify", t)[0]
    check(f"verify-refuse-{name}", rc2 == 1, rc2)


def write_all(z, m, skip=(), extra=None):
    for n, b in m.items():
        if n not in skip:
            z.writestr(n, b)
    for n, b in (extra or {}).items():
        z.writestr(n, b)


tamper("no-manifest", lambda z, m: write_all(z, m, skip=("manifest.json",)))
tamper("hash-mismatch", lambda z, m: write_all(z, dict(m, **{"mmis.db": m["mmis.db"][:-1] + bytes([m["mmis.db"][-1] ^ 1])})))
tamper("extra-member", lambda z, m: write_all(z, m, extra={"evil.txt": b"x"}))
tamper("traversal", lambda z, m: write_all(z, m, extra={"../escape.txt": b"x"}))
tamper("abs-path", lambda z, m: write_all(z, m, extra={"C:/Windows/Temp/mmis_escape.txt": b"x"}))


def newer(z, m):
    tmpdb = out / "tmp_newer.db"
    tmpdb.write_bytes(m["mmis.db"])
    c = sqlite3.connect(tmpdb)
    c.execute("PRAGMA user_version = 999")
    c.commit()
    c.close()
    db = tmpdb.read_bytes()
    man = json.loads(m["manifest.json"])
    s = json.dumps(man)
    old = hashlib.sha256(m["mmis.db"]).hexdigest()
    s = s.replace(old, hashlib.sha256(db).hexdigest())
    z.writestr("manifest.json", s)
    z.writestr("mmis.db", db)
    if "settings.json" in m:
        z.writestr("settings.json", m["settings.json"])


def corrupt(z, m):
    db = b"SQLite format 3\x00" + b"\x00" * 5000
    s = m["manifest.json"].decode().replace(hashlib.sha256(m["mmis.db"]).hexdigest(), hashlib.sha256(db).hexdigest())
    z.writestr("manifest.json", s)
    z.writestr("mmis.db", db)


tamper("schema-too-new", newer)
tamper("db-corrupt", corrupt)
check("no-escape-file", not (work / "escape.txt").exists() and not Path("C:/Windows/Temp/mmis_escape.txt").exists())
notzip = out / "notzip.zip"
notzip.write_bytes(b"hello")
rc, o, e = run("backup", "restore", notzip, "--yes")
check("refuse-notzip", rc == 1 and "Traceback" not in e, (rc, e[-200:]))

# ---- good restore over existing data creates a safety backup
before_files = {p_.name for p_ in Path(data).rglob("*") if p_.is_file()}
rc, o, e = run("backup", "restore", bk, "--yes")
check("restore-over-existing", rc == 0, (rc, e[-300:]))
new_zips = [p_ for p_ in Path(data).rglob("*.zip") if p_.name not in before_files] + [p_ for p_ in work.rglob("*safety*") if p_.is_file()]
print("safety backup candidates:", [str(p_.relative_to(work)) for p_ in new_zips], "| restore output:", o.strip()[-200:])
check("safety-backup", bool(new_zips) and all(run("backup", "verify", z_)[0] == 0 for z_ in new_zips[:1]))
rc, o, e = run("profile", "history", PROFILE, "--json")
check("restored-state", rc == 0 and o.count("scanned_at") == 2, o[:200])

shutil.rmtree(work)
print("FAILED:", fails)
sys.exit(1 if fails else 0)
