# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os


root = Path(os.environ.get("FLOWBOARD_ROOT", Path.cwd())).resolve()
obf_root = Path(os.environ.get("FLOWBOARD_OBF_ROOT", root / "build" / "pyarmor")).resolve()
entry = obf_root / "flowboard" / "launcher.py"
if not entry.exists():
    entry = root / "agent" / "flowboard" / "launcher.py"
    pathex = [str(root / "agent")]
else:
    pathex = [str(obf_root), str(root / "agent")]

frontend_dist = root / "frontend" / "dist"
datas = []
if frontend_dist.exists():
    datas.append((str(frontend_dist), "frontend_dist"))

hiddenimports = [
    "flowboard.main",
    "uvicorn.loops.auto",
    "uvicorn.lifespan.on",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "webview.platforms.winforms",
]

a = Analysis(
    [str(entry)],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Flowboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=os.environ.get("FLOWBOARD_CONSOLE", "0") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
