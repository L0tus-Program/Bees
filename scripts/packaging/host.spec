# Executado pelo PyInstaller fixado no grupo packaging, no Windows x64.
from pathlib import Path
import json

root = Path(SPECPATH).parents[1]
a = Analysis(
    [str(root / "scripts/packaging/host_entry.py")],
    pathex=[str(root / "apps/host/src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["bees_api", "bees_core", "bees_worker", "apsw", "fastapi", "uvicorn",
              "pytest", "ruff", "httpx2", "httpcore2", "tkinter", "setuptools",
              "pkg_resources", "pygments", "tzdata", "IPython", "rich", "email_validator",
              "_pytest", "anyio.pytest_plugin", "httpx._main", "click", "colorama",
              "pluggy", "iniconfig"],
    noarchive=False,
)
modules = sorted({item[0] for item in a.pure})
if any(name.split('.')[0] in {'bees_api', 'bees_core', 'bees_worker', 'apsw', 'fastapi', 'uvicorn',
                             'pytest', '_pytest', 'PyInstaller'}
       for name in modules):
    raise ValueError('O helper não pode incorporar o plano de controle')
(Path(DISTPATH).parent / 'module-inventory.json').write_text(
    json.dumps(modules, indent=2) + '\n', encoding='utf-8')
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="bees-host", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    disable_windowed_traceback=False, uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="bees-host")
