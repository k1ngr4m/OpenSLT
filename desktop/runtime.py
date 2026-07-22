from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet


def application_root() -> Path:
    override = os.getenv("OPENSLT_PORTABLE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def ensure_environment(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "data" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    env_file = root / ".env"
    if not env_file.exists():
        env_file.write_text(
            "\n".join(
                [
                    "ENVIRONMENT=desktop",
                    "PORTABLE_MODE=true",
                    "ENABLE_INTERNAL_SCHEDULER=true",
                    "EXECUTION_MODE=simulated",
                    "OPENSLT_DATA_ROOT=./data",
                    "OPENSLT_LOG_DIR=./logs",
                    "OPENSLT_API_PORT=8765",
                    "DATABASE_URL=sqlite:///./data/openslt.sqlite3",
                    "ARTIFACT_ROOT=./data/artifacts",
                    "LOG_DIR=./logs",
                    "HOST=127.0.0.1",
                    "PORT=8765",
                    "OPEN_BROWSER=false",
                    f"JWT_SECRET={secrets.token_urlsafe(48)}",
                    f"CREDENTIAL_ENCRYPTION_KEY={Fernet.generate_key().decode()}",
                    "INITIAL_ADMIN_USERNAME=admin",
                    "INITIAL_ADMIN_PASSWORD=shengli123",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return env_file


def find_available_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(probe.getsockname()[1])
    raise RuntimeError("无法分配本地 API 端口")


def run_backend(port: int, root: Path | None = None) -> None:
    root = (root or application_root()).resolve()
    os.chdir(root)
    if not getattr(sys, "frozen", False):
        backend_path = str(Path(__file__).resolve().parents[1] / "backend")
        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)
    os.environ.update(
        {
            "ENVIRONMENT": "desktop",
            "PORTABLE_MODE": "true",
            "ENABLE_INTERNAL_SCHEDULER": "true",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "OPENSLT_API_PORT": str(port),
            "OPEN_BROWSER": "false",
            "FRONTEND_DIST": "",
        }
    )
    from app.core.config import get_settings

    get_settings.cache_clear()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        log_config=None,
    )


class BackendProcess:
    def __init__(self, root: Path | None = None, preferred_port: int = 8765) -> None:
        self.root = (root or application_root()).resolve()
        self.port = find_available_port(preferred_port)
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 30.0) -> None:
        ensure_environment(self.root)
        env = os.environ.copy()
        env.update(
            {
                "ENVIRONMENT": "desktop",
                "OPENSLT_PORTABLE_ROOT": str(self.root),
                "PORTABLE_MODE": "true",
                "ENABLE_INTERNAL_SCHEDULER": "true",
                "HOST": "127.0.0.1",
                "PORT": str(self.port),
                "OPENSLT_API_PORT": str(self.port),
                "OPEN_BROWSER": "false",
                "FRONTEND_DIST": "",
            }
        )
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--backend", "--port", str(self.port)]
        else:
            source_root = str(Path(__file__).resolve().parents[1])
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (source_root, env.get("PYTHONPATH", ""))))
            command = [sys.executable, "-m", "desktop.main", "--backend", "--port", str(self.port)]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen(command, cwd=self.root, env=env, creationflags=flags)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"本地服务启动失败，退出码 {self.process.returncode}")
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=0.5) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.2)
        self.stop()
        raise TimeoutError("本地服务启动超时")

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        finally:
            self.process = None
