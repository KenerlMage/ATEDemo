# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 形态，排除未使用的 Qt 模块以控制体积。

打包：
    pyinstaller camera_assistant.spec --noconfirm
产物：
    dist/CameraAssistant/CameraAssistant.exe
"""

hiddenimports = [
    "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg",
]

excludes = [
    # 本工程不使用 WebEngine / 3D / 多媒体，排除可显著减小体积
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    # 避免同时打包其它绑定
    "PyQt5",
    "PyQt6",
    "PySide2",
    "tkinter",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CameraAssistant",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CameraAssistant",
)
