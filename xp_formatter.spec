# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the XPression As Run Log Formatter
# Build:  pyinstaller xp_formatter.spec
# Output: dist/XPressionAsRunFormatter.exe  (single-file, windowed)

block_cipher = None

a = Analysis(
    ['xp_formatter.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'PIL.BmpImagePlugin',
        'PIL.PngImagePlugin',
        'reportlab',
        'reportlab.pdfgen',
        'reportlab.lib',
        'reportlab.lib.pagesizes',
        'reportlab.lib.units',
        'reportlab.lib.colors',
        'reportlab.lib.styles',
        'reportlab.lib.enums',
        'reportlab.platypus',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'pandas', 'matplotlib', 'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='XPressionAsRunFormatter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='app.ico',       # uncomment and provide app.ico to set an icon
)
