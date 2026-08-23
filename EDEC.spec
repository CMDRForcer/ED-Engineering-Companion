# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).resolve()

analysis = Analysis(
    [str(project_root / "phase14_main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "Main.qml"), "."),
        (str(project_root / "qml"), "qml"),
        (str(project_root / "assets"), "assets"),
        (str(project_root / "ed_data"), "ed_data"),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "README.md"), "."),
    ],
    hiddenimports=[
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "zmq.backend.cython._zmq",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="EDEC",
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

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EDEC",
)
