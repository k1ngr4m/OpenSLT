from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path.cwd()
frontend_dist = root / "frontend" / "dist"
if not (frontend_dist / "index.html").is_file():
    raise SystemExit("frontend/dist is missing; run the frontend production build first")

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("asyncssh")
    + [
        "app.main",
        "app.models.domain",
        "app.api.router",
        "app.services.orchestration",
        "app.services.reports",
        "pydantic_settings",
    ]
)

datas = [
    (str(frontend_dist), "frontend/dist"),
    *collect_data_files("openpyxl"),
]

a = Analysis(
    [str(root / "backend" / "portable_main.py")],
    pathex=[str(root / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "celery",
        "redis",
        "pandas",
        "weasyprint",
        "pymysql",
        "pytest",
        "matplotlib",
        "numpy",
    ],
    noarchive=False,
    optimize=1,
)
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
    console=True,
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
    upx=True,
    upx_exclude=[],
    name="OpenSLT-Portable",
)
