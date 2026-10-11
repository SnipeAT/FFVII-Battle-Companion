# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec of the desktop app. Run through build_tools/build_exe.py (Rebuild_EXE.bat).
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

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
]

# Include RapidOCR ONNX models and yaml configuration.
# The text-angle classifier model is never loaded (engine/ocr_engine.py runs det + rec only).
datas += [d for d in collect_data_files('rapidocr_onnxruntime') if 'cls_infer' not in os.path.basename(d[0])]

# Collect submodules to prevent runtime ImportError in frozen bundle. Every module of the
# ffvii_companion package is included, so new modules need no entry here.
hidden_imports = (
    collect_submodules('uvicorn') +
    # Without webview.__pyinstaller: its build hook imports PyInstaller, whose code (GPL, apart
    # from the bootloader) must not end up in the EXE
    collect_submodules('webview', filter=lambda name: '__pyinstaller' not in name) +
    collect_submodules('rapidocr_onnxruntime') +
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
    # watchfiles is only used by uvicorn's --reload; tests/tools are development-only
    excludes=['tkinter', 'matplotlib', 'scipy', 'unittest', 'test', 'tests', 'tools', 'build_tools', 'pydoc',
              'pdb', 'distutils', 'watchfiles'],
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
