"""Master-side independent fixture: builds a sample modpack. Usage: build_pack.py <dir>"""
import io
import json
import sys
import zipfile
from pathlib import Path


def jar(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(entries.items()):
            z.writestr(name, data)
    return buf.getvalue()


root = Path(sys.argv[1])
mods = root / "mods"
mods.mkdir(parents=True)
(root / "config" / "alpha").mkdir(parents=True)
(root / "config" / "alpha-client.toml").write_text("x=1")
(root / "config" / "alpha" / "main.json").write_text("{}")
(root / "config" / "unrelated.toml").write_text("x=1")

nested = jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "nestedlib", "version": "0.3.0"})})

# 1. full fabric mod with nested jar, provides, all dependency kinds
(mods / "alpha-1.2.3.jar").write_bytes(jar({
    "fabric.mod.json": json.dumps({
        "schemaVersion": 1, "id": "alpha", "version": "1.2.3", "name": "Alpha Mod",
        "description": "desc", "authors": ["Ann", {"name": "Bob"}], "license": "MIT",
        "environment": "client", "provides": ["alpha_api"],
        "depends": {"minecraft": "1.20.1", "fabricloader": ">=0.15.0", "beta": ["^2.0.0", "1.9.x"]},
        "recommends": {"gamma": "*"}, "suggests": {"delta": ">=1"},
        "breaks": {"badmod": "<3.0.0"}, "conflicts": {"meh": "*"},
        "jars": [{"file": "META-INF/jars/nestedlib-0.3.0.jar"}],
    }),
    "META-INF/jars/nestedlib-0.3.0.jar": nested,
}))

# 2. fabric mod with missing name/version and lenient JSON (raw newline inside a string)
(mods / "lenient.jar").write_bytes(jar({
    "fabric.mod.json": '{"schemaVersion":1,"id":"lenient","description":"line1\nline2\ttab"}',
}))

# 3. forge multi-mod with placeholder resolved from manifest
(mods / "forgepair.jar").write_bytes(jar({
    "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\nImplementation-Version: 4.5.6\r\n\r\n",
    "META-INF/mods.toml": '''
modLoader="javafml"
loaderVersion="[47,)"
license="LGPL"
[[mods]]
modId="forgeone"
version="${file.jarVersion}"
displayName="Forge One"
[[mods]]
modId="forgetwo"
version="1.0"
[[dependencies.forgeone]]
modId="minecraft"
mandatory=true
versionRange="[1.20.1]"
ordering="NONE"
side="BOTH"
[[dependencies.forgeone]]
modId="optlib"
mandatory=false
versionRange="[1.0,2.0)"
''',
}))

# 4. forge placeholder unresolved (no manifest)
(mods / "noversion.jar").write_bytes(jar({
    "META-INF/mods.toml": 'modLoader="javafml"\nloaderVersion="[47,)"\n[[mods]]\nmodId="noversion"\nversion="${file.jarVersion}"\n',
}))

# 5. neoforge with incompatible/discouraged
(mods / "neo.jar").write_bytes(jar({
    "META-INF/neoforge.mods.toml": '''
modLoader="javafml"
loaderVersion="[1,)"
[[mods]]
modId="neo"
version="2.0.0"
[[dependencies.neo]]
modId="other"
type="incompatible"
versionRange="[1,)"
[[dependencies.neo]]
modId="meh2"
type="discouraged"
[[dependencies.neo]]
modId="req"
type="required"
versionRange="[3.1,)"
''',
}))

# 6. quilt
(mods / "quilty.jar").write_bytes(jar({
    "quilt.mod.json": json.dumps({"schema_version": 1, "quilt_loader": {
        "group": "g", "id": "quilty", "version": "0.1.0",
        "metadata": {"name": "Quilty"},
        "provides": ["quilty_api", {"id": "quilty_extra", "version": "1.0"}],
        "depends": ["simple_dep", {"id": "qdep", "versions": ">=1.0.0"}, {"id": "qopt", "optional": True}],
        "breaks": [{"id": "qbad", "versions": "*"}],
    }}),
}))

# 7-11. bad inputs
(mods / "corrupt.jar").write_bytes(b"this is not a zip")
(mods / "empty.jar").write_bytes(jar({"README.txt": "nothing"}))
(mods / "legacy.jar").write_bytes(jar({"mcmod.info": "[]"}))
(mods / "badjson.jar").write_bytes(jar({"fabric.mod.json": "{not json"}))
(mods / "badtoml.jar").write_bytes(jar({"META-INF/mods.toml": "[[mods\nbroken"}))
(mods / "off.jar.disabled").write_bytes(jar({"fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "off", "version": "1"})}))
(mods / "notes.txt").write_text("ignore me")
print("built", root)
