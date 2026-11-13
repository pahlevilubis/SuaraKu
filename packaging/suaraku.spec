# -*- mode: python ; coding: utf-8 -*-
# Resep PyInstaller untuk SuaraKu (mode folder / onedir, tanpa jendela console).
# Jalankan dari root repo:  pyinstaller packaging/suaraku.spec --noconfirm --clean

import os

from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, collect_submodules,
)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH dari PyInstaller)
ICON = os.path.join(ROOT, "assets", "icon.ico")

hidden = collect_submodules("edge_tts") + collect_submodules("suaraku")
datas = [
    (os.path.join(ROOT, "assets", "icon.png"), "assets"),
    (ICON, "assets"),
    (os.path.join(ROOT, "assets", "fonts"), os.path.join("assets", "fonts")),
]
datas += collect_data_files("certifi")
binaries = []

# Suara → teks (faster-whisper + CTranslate2 + PyAV + onnxruntime untuk deteksi suara).
for pkg in ("faster_whisper", "ctranslate2", "av"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("huggingface_hub") + collect_submodules("tokenizers")
# onnxruntime dipanggil faster-whisper di dalam fungsi, jadi tidak terdeteksi otomatis.
hidden += ["onnxruntime", "onnxruntime.capi", "onnxruntime.capi._pybind_state"]
binaries += collect_dynamic_libs("onnxruntime")
datas += collect_data_files("onnxruntime", excludes=["**/transformers/**", "**/tools/**"])

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SuaraKu",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SuaraKu",
)
