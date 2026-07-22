from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path.cwd()
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("asyncssh")
    + [
        "desktop.main",
        "desktop.pages",
        "desktop.api_client",
        "desktop.runtime",
        "app.main",
        "app.models.domain",
        "app.api.router",
        "app.services.orchestration",
        "app.services.reports",
        "pydantic_settings",
    ]
)

datas = [*collect_data_files("openpyxl")]

a = Analysis(
    [str(root / "desktop" / "main.py")],
    pathex=[str(root), str(root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["celery", "redis", "pandas", "weasyprint", "pymysql", "pytest", "matplotlib", "numpy"],
    noarchive=False,
    optimize=1,
)

# Do not let DLLs from Anaconda/other PATH entries shadow Windows ICU or the
# OpenSSL build which belongs to the Python runtime used for packaging.
excluded_runtime_dlls = {"icuuc.dll", "icudt73.dll", "libcrypto-3-x64.dll", "libssl-3-x64.dll"}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in excluded_runtime_dlls]
python_dll_dir = Path(sys.base_prefix) / "DLLs"
for dll_name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll"):
    dll_path = python_dll_dir / dll_name
    if not dll_path.is_file():
        raise SystemExit(f"Required Python runtime DLL is missing: {dll_path}")
    a.binaries.append((dll_name, str(dll_path), "BINARY"))

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenSLT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name="OpenSLT-Desktop-windows-x64")
