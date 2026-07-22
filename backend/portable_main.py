"""OpenSLT Windows portable-edition launcher."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from cryptography.fernet import Fernet


def portable_root() -> Path:
    override = os.getenv("OPENSLT_PORTABLE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def ensure_portable_environment(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    env_file = root / ".env"
    if not env_file.exists():
        env_file.write_text(
            "\n".join(
                [
                    "ENVIRONMENT=portable",
                    "PORTABLE_MODE=true",
                    "ENABLE_INTERNAL_SCHEDULER=true",
                    "EXECUTION_MODE=simulated",
                    "DATABASE_URL=sqlite:///./data/openslt.sqlite3",
                    "ARTIFACT_ROOT=./data/artifacts",
                    "LOG_DIR=./logs",
                    "LOG_LEVEL=INFO",
                    "HOST=127.0.0.1",
                    "PORT=8765",
                    "OPEN_BROWSER=true",
                    f"JWT_SECRET={secrets.token_urlsafe(48)}",
                    f"CREDENTIAL_ENCRYPTION_KEY={Fernet.generate_key().decode()}",
                    "INITIAL_ADMIN_USERNAME=admin",
                    "INITIAL_ADMIN_PASSWORD=shengli123",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    os.environ.setdefault("PORTABLE_MODE", "true")
    os.environ.setdefault("ENABLE_INTERNAL_SCHEDULER", "true")
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS")).resolve()
        os.environ.setdefault("FRONTEND_DIST", str(bundle_root / "frontend" / "dist"))


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex((host, port)) != 0


def open_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.25)


def main() -> None:
    root = portable_root()
    ensure_portable_environment(root)

    from app.core.config import settings

    url = f"http://{settings.host}:{settings.port}"
    if not port_is_available(settings.host, settings.port):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        raise SystemExit(f"端口 {settings.port} 已被其他程序占用，请修改 .env 中的 PORT。")

    if settings.open_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    print("=" * 58)
    print("OpenSLT 免安装版已启动")
    print(f"访问地址: {url}")
    print("关闭程序: 在此窗口按 Ctrl+C")
    print("=" * 58)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
