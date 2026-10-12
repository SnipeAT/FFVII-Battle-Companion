# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of the desktop app. Run through build_tools/build_exe.py.
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # build_tools/ -> project root
SRC = ROOT / 'src'
PKG = SRC / 'ffvii_companion'

# Read-only resources inside the package; ffvii_companion.paths.PACKAGE_DIR finds them at
# <_MEIPASS>/ffvii_companion/ in the bundle
datas = [
    (str(PKG / 'data' / 'enemies.json'), 'ffvii_companion/data'),
    (str(PKG / 'data' / 'formations.json'), 'ffvii_companion/data'),
    (str(PKG / 'server' / 'static'), 'ffvii_companion/server/static'),
    # The OCR models, with their license (Apache-2.0, unlike the rest of the app)
    (str(PKG / 'ppocr' / 'ch_PP-OCRv4_det_infer.onnx'), 'ffvii_companion/ppocr'),
    (str(PKG / 'ppocr' / 'ch_PP-OCRv4_rec_infer.onnx'), 'ffvii_companion/ppocr'),
    (str(PKG / 'ppocr' / 'LICENSE.txt'), 'ffvii_companion/ppocr'),
]

# Collect submodules to prevent runtime ImportError in frozen bundle. Every module of the
# ffvii_companion package is included, so new modules need no entry here.
hidden_imports = (
    collect_submodules('uvicorn') +
    # Without webview.__pyinstaller: its build hook imports PyInstaller, whose code (GPL, apart
    # from the bootloader) must not end up in the EXE
    collect_submodules('webview', filter=lambda name: '__pyinstaller' not in name) +
    collect_submodules('ffvii_companion') +
    [
        'clr_loader',
        'pythonnet',
        'rapidfuzz',
        'cv2',
        'mss',
        'fastapi',
        'starlette',
    ]
)

# Deduplicate hidden imports
hidden_imports = list(dict.fromkeys(hidden_imports))

a = Analysis(
    [str(PKG / '__main__.py')],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # watchfiles is only used by uvicorn's --reload; tests/tools are development-only. setuptools
    # and distutils come in only through cffi's compiler helpers (clr_loader uses cffi without
    # them). Since Python 3.12 distutils is setuptools' copy, which PyInstaller aliases to the name
    # distutils itself: excluding 'distutils' there fails the build, excluding setuptools drops both.
    excludes=['tkinter', 'matplotlib', 'scipy', 'unittest', 'test', 'tests', 'tools', 'build_tools', 'pydoc',
              'pdb', 'setuptools', 'watchfiles'] + (['distutils'] if sys.version_info < (3, 12) else []),
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# OpenCV's FFmpeg video plugin (30 MB, LGPL FFmpeg): the app decodes no video, and cv2 loads the
# plugin only for video capture
a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).lower().startswith('opencv_videoio_ffmpeg')]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

manifest_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2, PerMonitor</dpiAwareness>
    </windowsSettings>
  </application>
</assembly>'''

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FFVII_Companion',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False, # Windowed application, no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    manifest=manifest_xml,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FFVII_Companion',
)
