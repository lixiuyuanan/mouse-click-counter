# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['../mouse_counter.py'],
    pathex=[],
    binaries=[],
    datas=[('D:/PYCH/AI agent/鼠标点击次数记录/icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='鼠标点击计数V2.4.4',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='D:/PYCH/AI agent/鼠标点击次数记录/version_info.txt',
    icon=['D:/PYCH/AI agent/鼠标点击次数记录/icon.ico'],
)
