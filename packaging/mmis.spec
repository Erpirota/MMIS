# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for Minecraft Mod Intelligence Studio (MMIS).

Builds two executables sharing a single bundle directory:
- MMIS.exe (desktop windowed GUI)
- mmis-cli.exe (command line interface)
"""

import os

src_dir = os.path.normpath(os.path.join(SPECPATH, "..", "src"))
assets_pattern = os.path.normpath(os.path.join(src_dir, "mmis", "ui", "assets", "*.svg"))

datas = [
    (assets_pattern, "mmis/ui/assets"),
]

hiddenimports = [
    "PySide6.QtSvg",
]

excludes = [
    "tkinter",
    "unittest",
    "pytest",
]

a_gui = Analysis(
    [os.path.join(SPECPATH, "mmis_gui.py")],
    pathex=[src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

a_cli = Analysis(
    [os.path.join(SPECPATH, "mmis_cli.py")],
    pathex=[src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

MERGE((a_gui, "mmis_gui", "MMIS"), (a_cli, "mmis_cli", "mmis-cli"))

pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="MMIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="mmis-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MMIS",
)
