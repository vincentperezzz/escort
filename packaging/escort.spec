# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).resolve().parent
ui = root / "desktop" / "ui" / "dist"
datas = []
if (ui / "index.html").exists():
    datas.append((str(ui), "ui"))

hiddenimports = collect_submodules("escort") + [
    "escort.desktop.secret_store",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "pypdf",
    "docx",
    "webview",
    "keyring",
    "pystray",
    "PIL",
    "secrets",
    # Live capture imports these lazily so the app still starts without them.
    "mss",
    "pyaudiowpatch",
    "websockets",
]

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
# Never ship a local secrets.py as the stdlib secrets module.
a.pure = [
    item
    for item in a.pure
    if not (item[0] == "secrets" and "escort" in str(item[1]).replace("\\", "/"))
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Escort",
    console=False,
    icon=str(root / "packaging" / "escort.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    name="Escort",
)
