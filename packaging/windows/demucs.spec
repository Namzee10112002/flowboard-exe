# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


root = Path(os.environ.get("FLOWBOARD_ROOT", Path.cwd())).resolve()
entry = root / "agent" / "flowboard" / "tools" / "demucs_soundfile.py"

datas = []
binaries = []
hiddenimports = [
    "demucs.separate",
    "demucs.pretrained",
    "demucs.htdemucs",
    "demucs.hdemucs",
    "demucs.demucs",
    "openunmix",
    "numpy.core",
    "numpy.core.multiarray",
    "numpy._core",
    "numpy._core.multiarray",
    "soundfile",
    "torchaudio",
]

for package in ("demucs", "dora", "openunmix"):
    hiddenimports += collect_submodules(package)
    datas += collect_data_files(package)

for package in ("torch", "torchaudio", "soundfile"):
    binaries += collect_dynamic_libs(package)
    datas += collect_data_files(package)

a = Analysis(
    [str(entry)],
    pathex=[str(root / "agent")],
    binaries=binaries,
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
    name="demucs_soundfile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
