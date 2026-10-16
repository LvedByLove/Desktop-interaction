# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('models', 'models'), ('scripts', 'scripts'), ('src', 'src'), ('libs', 'libs'), ('assets', 'assets')],
    hiddenimports=[
        'colorlog', 'qasync', 'PyQt5', 'pyperclip', 'win32com', 'win32com.client',
        # openai 的传递依赖链：缺失会让 vl_camera 导入失败，进而在打包产物里
        # 拖垮 MCP 工具注册与 initialize 握手
        'openai', 'httpx', 'httpcore', 'h11', 'anyio', 'sniffio', 'distro', 'jiter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'PySide2', 'PyQt6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Desktop interaction',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\image\\Redstone.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Desktop interaction',
)
