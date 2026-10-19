# PyInstaller spec file for PDF to Markdown (Windows build).
#
# Build on a Windows machine with:
#   pip install -r requirements.txt
#   pyinstaller pdf2md.spec
#
# Output: dist\PDF2Markdown\PDF2Markdown.exe (folder build, faster startup)
# For a single-file exe instead, change EXE(... ) below per the comment.

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['src/pdf2md/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['pdf2md.main_window', 'pdf2md.worker', 'pdf2md.converter'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDF2Markdown',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # set True temporarily if you need to see errors
    icon=None,               # put an .ico path here if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PDF2Markdown',
)

# ---- Single-file build (slower startup, one .exe) ----
# Comment out the EXE/COLLECT block above and use this instead:
#
# exe = EXE(
#     pyz,
#     a.scripts,
#     a.binaries,
#     a.zipfiles,
#     a.datas,
#     [],
#     name='PDF2Markdown',
#     debug=False,
#     bootloader_ignore_signals=False,
#     strip=False,
#     upx=True,
#     upx_exclude=[],
#     runtime_tmpdir=None,
#     console=False,
#     icon=None,
# )
