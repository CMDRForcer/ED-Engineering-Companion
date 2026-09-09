# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from ed_companion import APP_VERSION


project_root = Path(SPECPATH).resolve()
version_parts = [int(value) for value in APP_VERSION.split(".")]
version_tuple = tuple((version_parts + [0, 0, 0, 0])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_tuple,
        prodvers=version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [
                StringStruct("CompanyName", "ED Engineering Companion"),
                StringStruct("FileDescription", "ED Engineering Companion"),
                StringStruct("FileVersion", APP_VERSION),
                StringStruct("InternalName", "EDEC"),
                StringStruct("LegalCopyright", "GPL-3.0-or-later"),
                StringStruct("OriginalFilename", "EDEC.exe"),
                StringStruct("ProductName", "ED Engineering Companion"),
                StringStruct("ProductVersion", APP_VERSION),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

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
    version=version_info,
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
