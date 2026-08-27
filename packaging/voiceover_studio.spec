# PyInstaller spec: onedir bundle with two entry points sharing one dist —
#   localizator      (GUI, run_gui.py)
#   localizator-cli  (console, run_cli.py)
# plus static ffmpeg/ffprobe copied to third_party/ffmpeg/ inside the dist,
# where core.ffbin resolves them first. Build:
#   .venv-app/bin/pyinstaller packaging/localizator.spec --noconfirm
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# single source of truth for the app version
with open(os.path.join(ROOT, "voiceover_studio", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo)

    _vt = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
    WIN_VERSION = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_vt, prodvers=_vt),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("ProductName", "Voiceover Studio"),
                StringStruct("FileDescription", "Voiceover Studio"),
                StringStruct("FileVersion", VERSION),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ])
else:
    WIN_VERSION = None

GUI_NAME = "VoiceoverStudio" if sys.platform == "win32" else "voiceover-studio"
CLI_NAME = GUI_NAME + "-cli"

SFX = ".exe" if sys.platform == "win32" else ""
FF_DIR = os.path.join(ROOT, "third_party", "ffmpeg")
ffmpeg_binaries = [
    (os.path.join(FF_DIR, "ffmpeg" + SFX), "third_party/ffmpeg"),
    (os.path.join(FF_DIR, "ffprobe" + SFX), "third_party/ffmpeg"),
]
# ffplay powers the voice Preview button; bundle it when the fetch script provided it
if os.path.exists(os.path.join(FF_DIR, "ffplay" + SFX)):
    ffmpeg_binaries.append((os.path.join(FF_DIR, "ffplay" + SFX), "third_party/ffmpeg"))
ttk_datas = collect_data_files("ttkbootstrap")

a_gui = Analysis(
    [os.path.join(ROOT, "run_gui.py")],
    pathex=[ROOT],
    binaries=ffmpeg_binaries,
    datas=ttk_datas,
    # PIL._tkinter_finder is imported dynamically by PIL.ImageTk (via ttkbootstrap)
    hiddenimports=["PIL._tkinter_finder"],
    noarchive=False,
)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name=GUI_NAME,
    console=False,
    upx=False,
    version=WIN_VERSION,
)

a_cli = Analysis(
    [os.path.join(ROOT, "run_cli.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name=CLI_NAME,
    console=True,
    upx=False,
    version=WIN_VERSION,
)

coll = COLLECT(
    exe_gui, a_gui.binaries, a_gui.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    name="VoiceoverStudio",
    upx=False,
)
