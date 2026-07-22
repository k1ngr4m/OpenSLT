from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from desktop.models import ApiError


class ApiClient:
    """Thread-safe HTTP client for the loopback OpenSLT API."""

    def __init__(self, base_url: str, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = ""
        self.refresh_token = ""
        self._client = httpx.Client(base_url=f"{self.base_url}/api/v1", timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def login(self, username: str, password: str) -> dict[str, Any]:
        tokens = self.request("POST", "/auth/login", json={"username": username, "password": password}, authenticate=False)
        self._set_tokens(tokens)
        return self.get("/auth/me")

    def logout(self) -> None:
        try:
            if self.refresh_token:
                self.post("/auth/logout", json={"refresh_token": self.refresh_token})
        finally:
            self.access_token = ""
            self.refresh_token = ""

    def _set_tokens(self, payload: dict[str, Any]) -> None:
        self.access_token = str(payload.get("access_token", ""))
        self.refresh_token = str(payload.get("refresh_token", ""))

    def _refresh(self) -> bool:
        if not self.refresh_token:
            return False
        response = self._client.post("/auth/refresh", json={"refresh_token": self.refresh_token})
        if response.is_success:
            self._set_tokens(response.json())
            return True
        self.access_token = ""
        self.refresh_token = ""
        return False

    def request(self, method: str, path: str, *, authenticate: bool = True, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if authenticate and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        response = self._client.request(method, path, headers=headers, **kwargs)
        if authenticate and response.status_code == 401 and self._refresh():
            headers["Authorization"] = f"Bearer {self.access_token}"
            response = self._client.request(method, path, headers=headers, **kwargs)
        if not response.is_success:
            self._raise_error(response)
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.content

    @staticmethod
    def _raise_error(response: httpx.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raise ApiError(
            code=str(payload.get("code", "HTTP_ERROR")),
            message=str(payload.get("message") or response.reason_phrase or "请求失败"),
            trace_id=payload.get("trace_id") or response.headers.get("x-trace-id"),
            status_code=response.status_code,
            details=payload.get("details"),
        )

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        clean = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        return self.request("GET", path, params=clean)

    def post(self, path: str, *, json: Any = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, *, json: Any = None) -> Any:
        return self.request("PUT", path, json=json)

    def patch(self, path: str, *, json: Any = None) -> Any:
        return self.request("PATCH", path, json=json)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)

    def download(self, path: str, destination: Path) -> Path:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        with self._client.stream("GET", path, headers=headers) as response:
            if not response.is_success:
                response.read()
                self._raise_error(response)
            with destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        return destination

    def download_info(self, path: str) -> tuple[bytes, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = self._client.get(path, headers=headers)
        if not response.is_success:
            self._raise_error(response)
        disposition = response.headers.get("content-disposition", "")
        match = re.search(r'filename="?([^";]+)', disposition)
        return response.content, (match.group(1) if match else "download.bin")

    def websocket_url(self, run_id: int) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        host = self.base_url.split("://", 1)[-1]
        return f"{scheme}://{host}/api/v1/ws/runs/{run_id}?token={quote(self.access_token)}"
