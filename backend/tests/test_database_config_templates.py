from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models import AuditLog


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_private_template_crud_and_audit(
    client: TestClient,
    admin_headers: dict[str, str],
):
    created = client.post(
        "/api/v1/database-config-templates",
        headers=admin_headers,
        json={"name": "  核心配置  ", "keys": ["SETTING_A", "SETTING_B"]},
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["name"] == "核心配置"
    assert template["keys"] == ["SETTING_A", "SETTING_B"]

    duplicate = client.post(
        "/api/v1/database-config-templates",
        headers=admin_headers,
        json={"name": "核心配置", "keys": ["SETTING_C"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "DATABASE_CONFIG_TEMPLATE_NAME_EXISTS"

    renamed = client.patch(
        f"/api/v1/database-config-templates/{template['id']}",
        headers=admin_headers,
        json={"new_name": "常用配置"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "常用配置"
    assert renamed.json()["keys"] == template["keys"]

    listed = client.get("/api/v1/database-config-templates", headers=admin_headers)
    assert [item["name"] for item in listed.json()] == ["常用配置"]

    client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "template_tester",
            "display_name": "模板测试员",
            "password": "tester-password",
            "role": "tester",
        },
    )
    tester_headers = _login(client, "template_tester", "tester-password")
    assert client.get(
        "/api/v1/database-config-templates", headers=tester_headers
    ).json() == []
    forbidden = client.patch(
        f"/api/v1/database-config-templates/{template['id']}",
        headers=tester_headers,
        json={"new_name": "越权修改"},
    )
    assert forbidden.status_code == 404

    deleted = client.delete(
        f"/api/v1/database-config-templates/{template['id']}", headers=admin_headers
    )
    assert deleted.status_code == 204
    assert client.get(
        "/api/v1/database-config-templates", headers=admin_headers
    ).json() == []

    db = SessionLocal()
    try:
        actions = {
            item.action
            for item in db.query(AuditLog)
            .filter(AuditLog.object_type == "database_config_template")
            .all()
        }
        assert actions == {
            "database_config_template.create",
            "database_config_template.rename",
            "database_config_template.delete",
        }
    finally:
        db.close()


def test_template_payload_rejects_empty_duplicate_or_oversized_keys(
    client: TestClient,
    admin_headers: dict[str, str],
):
    for keys in ([], ["A", "A"], ["x" * 256]):
        response = client.post(
            "/api/v1/database-config-templates",
            headers=admin_headers,
            json={"name": "无效模板", "keys": keys},
        )
        assert response.status_code == 422
