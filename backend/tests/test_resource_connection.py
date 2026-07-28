from __future__ import annotations

import importlib

from app.core.database import SessionLocal
from app.models import AuditLog, Resource
from conftest import create_resource


def connection_payload(**overrides):
    payload = {
        "resource_type": "rem",
        "host": "127.0.0.1",
        "ssh_port": 22,
        "username": "tester",
        "auth_type": "password",
        "password": "",
        "private_key": "",
        "remote_path": "/tmp/openslt",
        "capabilities": {},
    }
    payload.update(overrides)
    return payload


def test_connection_test_uses_current_form_values_without_saving(
    client,
    admin_headers,
    monkeypatch,
):
    resource = create_resource(client, admin_headers, "REM-connection-test")
    calls = []

    class FakeSSHAdapter:
        async def check(self, **options):
            calls.append(options)
            return {"ok": True, "message": "SSH connection successful"}

    router_module = importlib.import_module("app.api.routes.resource_core")
    monkeypatch.setattr(router_module, "ssh_adapter", FakeSSHAdapter())

    edited = client.post(
        "/api/v1/resources/connection-test",
        headers=admin_headers,
        json=connection_payload(resource_id=resource["id"]),
    )
    assert edited.status_code == 200
    assert edited.json()["ok"] is True
    assert calls[0]["password"] == "secret"

    changed_without_password = client.post(
        "/api/v1/resources/connection-test",
        headers=admin_headers,
        json=connection_payload(resource_id=resource["id"], host="127.0.0.2"),
    )
    assert changed_without_password.status_code == 200
    assert changed_without_password.json() == {
        "ok": False,
        "message": "SSH 连接信息已修改，请重新输入 SSH 密码",
    }
    assert len(calls) == 1

    created = client.post(
        "/api/v1/resources/connection-test",
        headers=admin_headers,
        json=connection_payload(host="new.example.test", password="new-secret"),
    )
    assert created.status_code == 200
    assert created.json()["ok"] is True
    assert calls[1]["host"] == "new.example.test"
    assert calls[1]["password"] == "new-secret"

    db = SessionLocal()
    try:
        assert db.query(Resource).count() == 1
        stored = db.get(Resource, resource["id"])
        assert stored.host == "127.0.0.1"
        assert stored.health_status == "unknown"
        audits = db.query(AuditLog).filter(AuditLog.action == "resource.connection_test").all()
        assert [item.result for item in audits] == ["success", "failed", "success"]
    finally:
        db.close()


def test_connection_test_rejects_missing_stored_resource(client, admin_headers):
    response = client.post(
        "/api/v1/resources/connection-test",
        headers=admin_headers,
        json=connection_payload(resource_id=999_999),
    )
    assert response.status_code == 404
