from __future__ import annotations

import typing
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import _load_or_create_credential_encryption_key, _load_or_create_jwt_secret
from backend.portable_main import ensure_portable_environment
from conftest import create_resource


def test_login_refresh_and_role_boundary(client: TestClient, admin_headers: typing.Dict[str, str]):
    created = client.post("/api/v1/users", headers=admin_headers, json={"username": "viewer", "display_name": "访客", "password": "viewer-password", "role": "visitor"})
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert login.status_code == 200
    tokens = login.json()
    viewer_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/resources", headers=viewer_headers).status_code == 200
    assert client.post("/api/v1/plans", headers=viewer_headers, json={}).status_code == 403
    assert client.get("/api/v1/users", headers=viewer_headers).status_code == 403
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_errors_include_trace_id(client: TestClient):
    response = client.get("/api/v1/resources")
    assert response.status_code == 401
    assert response.json()["trace_id"]
    assert response.headers["x-trace-id"] == response.json()["trace_id"]


def test_slnic_resource_type_is_supported(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "SLNIC-01", resource_type="slnic")
    assert resource["resource_type"] == "slnic"


def test_jwt_secret_is_generated_and_persisted(tmp_path: Path):
    artifact_root = tmp_path / "data" / "artifacts"
    first = _load_or_create_jwt_secret(None, artifact_root)
    second = _load_or_create_jwt_secret(None, artifact_root)
    secret_file = artifact_root.parent / "secrets" / "jwt_secret"
    assert first
    assert first == second
    assert secret_file.read_text(encoding="utf-8") == first
    assert secret_file.stat().st_mode & 0o777 == 0o600
    assert _load_or_create_jwt_secret("compatibility-secret", artifact_root) == "compatibility-secret"


def test_credential_encryption_key_is_generated_validated_and_persisted(tmp_path: Path):
    artifact_root = tmp_path / "data" / "artifacts"
    first = _load_or_create_credential_encryption_key(None, artifact_root)
    second = _load_or_create_credential_encryption_key(None, artifact_root)
    secret_file = artifact_root.parent / "secrets" / "credential_encryption_key"
    Fernet(first.encode())
    assert first == second
    assert secret_file.read_text(encoding="utf-8") == first
    assert secret_file.stat().st_mode & 0o777 == 0o600

    configured = Fernet.generate_key().decode()
    assert _load_or_create_credential_encryption_key(configured, artifact_root) == configured
    with pytest.raises(ValueError, match="Fernet.generate_key"):
        _load_or_create_credential_encryption_key("replace-with-fernet-generate-key", artifact_root)


def test_portable_environment_does_not_write_jwt_secret(tmp_path: Path):
    ensure_portable_environment(tmp_path)
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "JWT_SECRET" not in content
    assert "CREDENTIAL_ENCRYPTION_KEY=" in content
