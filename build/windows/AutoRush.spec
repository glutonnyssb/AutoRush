# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller pour AutoRush (Windows).

Produit deux executables dans dist/AutoRush/ :

* ``AutoRush.exe``      - l'interface graphique (sans console)
* ``autorush-cli.exe``  - la ligne de commande

Utilisation :
    py -m PyInstaller build\\windows\\AutoRush.spec --noconfirm

ffmpeg n'est pas embarque : il est cherche au lancement (PATH, dossier
``ffmpeg`` a cote de l'exe, variable AUTORUSH_FFMPEG). Voir README.md.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

block_cipher = None

# --------------------------------------------------------------------------- #
# Donnees embarquees
# --------------------------------------------------------------------------- #
datas = [
    (str(PROJECT_ROOT / "autorush" / "gui" / "styles.qss"), "autorush/gui"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(PROJECT_ROOT / "LICENSE"), "."),
    (str(PROJECT_ROOT / "docs"), "docs"),
]

# Les modeles CTranslate2 de faster-whisper sont telecharges au premier
# lancement : rien a embarquer, mais il faut ses metadonnees.
hiddenimports = [
    "autorush.gui.app",
    "autorush.gui.main_window",
    "autorush.gui.worker",
    "autorush.transcription.whisper_backend",
    "autorush.export.preview",
    "soundfile",
    "numpy",
]

try:
    from PyInstaller.utils.hooks import collect_data_files, copy_metadata

    datas += copy_metadata("faster-whisper", recursive=True)
    datas += collect_data_files("faster_whisper")
except Exception as exc:  # pragma: no cover - faster-whisper absent du build
    print(f"[AutoRush.spec] faster-whisper non trouve, build sans : {exc}")

# On ecarte ce qui alourdit inutilement le paquet.
excludes = [
    "tkinter", "matplotlib", "pandas", "scipy", "IPython", "notebook",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtSql", "PySide6.QtTest",
]

# --------------------------------------------------------------------------- #
# Analyse commune
# --------------------------------------------------------------------------- #
gui_analysis = Analysis(
    [str(PROJECT_ROOT / "build" / "windows" / "entry_gui.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

cli_analysis = Analysis(
    [str(PROJECT_ROOT / "build" / "windows" / "entry_cli.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes + ["PySide6", "shiboken6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE(
    (gui_analysis, "entry_gui", "AutoRush"),
    (cli_analysis, "entry_cli", "autorush-cli"),
)

gui_pyz = PYZ(gui_analysis.pure, gui_analysis.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli_analysis.pure, cli_analysis.zipped_data, cipher=block_cipher)

icon_path = PROJECT_ROOT / "build" / "windows" / "autorush.ico"
icon = str(icon_path) if icon_path.exists() else None

gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="AutoRush",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # interface graphique : pas de console
    disable_windowed_traceback=False,
    icon=icon,
)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="autorush-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # ligne de commande : console visible
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.zipfiles,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.zipfiles,
    cli_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AutoRush",
)
