from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.time import beijing_now
from app.models import (
    AuditLog,
    PlanResource,
    Resource,
    ResourceLock,
    RunResource,
    ScenarioResource,
    TestPlan as PlanModel,
    User,
    WorkflowVersionResource,
)
from app.services.resource_relations import sync_plan_resources
from conftest import create_plan_scenario, create_resource, publish_workflow


def rem_payload(**overrides):
    payload = {
        "name": "REM 复制源",
        "resource_type": "rem",
        "business_code": "rem_two",
        "host": "rem-copy.example.test",
        "ssh_port": 2222,
        "username": "rem-user",
        "auth_type": "password",
        "password": "ssh-password",
        "private_key": "ssh-private-key",
        "remote_path": "/opt/rem",
        "capabilities": {"nested": {"ports": [5100, 5101]}},
        "trade_ip": "10.10.0.10",
        "trade_tcp_port": 10001,
        "trade_udp_port": 10002,
        "query_ip": "10.10.0.11",
        "query_port": 10003,
        "version_info": "2.0.0",
        "notes": "复制测试",
        "is_enabled": True,
    }
    payload.update(overrides)
    return payload


def database_payload(**overrides):
    payload = {
        "name": "数据库复制源",
        "resource_type": "database",
        "business_code": "fut_mm",
        "database_engine": "mysql",
        "database_connection_mode": "ssh_tunnel",
        "database_host": "db-copy.example.test",
        "database_port": 3307,
        "database_names": ["trading", "reporting"],
        "database_username": "db-user",
        "database_password": "database-password",
        "database_tls_enabled": True,
        "host": "jump-copy.example.test",
        "ssh_port": 2222,
        "username": "jump-user",
        "auth_type": "private_key",
        "private_key": "jump-private-key",
        "capabilities": {"nested": {"schema": "v1"}},
        "version_info": "8.0",
        "notes": "数据库复制测试",
        "is_enabled": False,
    }
    payload.update(overrides)
    return payload


def create_database(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json=database_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_copy_resource_preserves_configuration_credentials_and_resets_health(
    client: TestClient,
    admin_headers: dict[str, str],
):
    created = client.post("/api/v1/resources", headers=admin_headers, json=rem_payload())
    assert created.status_code == 201, created.text
    source = created.json()

    db = SessionLocal()
    try:
        stored = db.get(Resource, source["id"])
        assert stored is not None
        stored.health_status = "healthy"
        stored.health_checked_at = beijing_now()
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/resources/{source['id']}/copy", headers=admin_headers)

    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["id"] != source["id"]
    assert copied["name"] == "REM 复制源 - 副本"
    assert copied["resource_type"] == "rem"
    assert copied["business_code"] == "rem_two"
    assert copied["host"] == "rem-copy.example.test"
    assert copied["ssh_port"] == 2222
    assert copied["username"] == "rem-user"
    assert copied["remote_path"] == "/opt/rem"
    assert copied["capabilities"] == {"nested": {"ports": [5100, 5101]}}
    assert copied["trade_ip"] == "10.10.0.10"
    assert copied["trade_tcp_port"] == 10001
    assert copied["trade_udp_port"] == 10002
    assert copied["query_ip"] == "10.10.0.11"
    assert copied["query_port"] == 10003
    assert copied["version_info"] == "2.0.0"
    assert copied["notes"] == "复制测试"
    assert copied["is_enabled"] is True
    assert copied["health_status"] == "unknown"
    assert copied["health_checked_at"] is None
    for secret_field in (
        "password",
        "private_key",
        "database_password",
        "encrypted_password",
        "encrypted_private_key",
        "encrypted_database_password",
    ):
        assert secret_field not in copied

    db = SessionLocal()
    try:
        original = db.get(Resource, source["id"])
        duplicate = db.get(Resource, copied["id"])
        assert original is not None
        assert duplicate is not None
        assert duplicate.encrypted_password == original.encrypted_password
        assert duplicate.encrypted_private_key == original.encrypted_private_key
        assert duplicate.encrypted_database_password == original.encrypted_database_password
    finally:
        db.close()


def test_copy_resource_keeps_suffix_within_database_name_limit(
    client: TestClient,
    admin_headers: dict[str, str],
):
    source_name = "资" * 128
    created = client.post(
        "/api/v1/resources",
        headers=admin_headers,
        json=rem_payload(name=source_name),
    )
    assert created.status_code == 201, created.text

    response = client.post(
        f"/api/v1/resources/{created.json()['id']}/copy",
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    copied_name = response.json()["name"]
    assert copied_name == f"{source_name[:123]} - 副本"
    assert len(copied_name) == 128


def test_copy_database_resource_preserves_database_credentials_and_disabled_state(
    client: TestClient,
    admin_headers: dict[str, str],
):
    source = create_database(client, admin_headers)

    response = client.post(f"/api/v1/resources/{source['id']}/copy", headers=admin_headers)

    assert response.status_code == 201, response.text
    copied = response.json()
    assert copied["name"] == "数据库复制源 - 副本"
    assert copied["database_connection_mode"] == "ssh_tunnel"
    assert copied["database_host"] == "db-copy.example.test"
    assert copied["database_port"] == 3307
    assert copied["database_names"] == ["trading", "reporting"]
    assert copied["database_username"] == "db-user"
    assert copied["database_tls_enabled"] is True
    assert copied["has_database_password"] is True
    assert copied["is_enabled"] is False

    db = SessionLocal()
    try:
        original = db.get(Resource, source["id"])
        duplicate = db.get(Resource, copied["id"])
        assert original is not None
        assert duplicate is not None
        assert duplicate.encrypted_private_key == original.encrypted_private_key
        assert duplicate.encrypted_database_password == original.encrypted_database_password
    finally:
        db.close()


def test_copy_resource_does_not_copy_relations_or_locks(
    client: TestClient,
    admin_headers: dict[str, str],
):
    source = create_resource(client, admin_headers, "REM 关系复制源")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[source["id"]])
    with SessionLocal() as db:
        plan_model = db.get(PlanModel, plan["id"])
        assert plan_model is not None
        sync_plan_resources(plan_model, [source["id"]], db)
        db.commit()

    publish_workflow(
        client,
        admin_headers,
        scenario,
        [source["id"]],
        [
            {
                "node_key": "wiring",
                "node_type": "wiring_confirmation",
                "name": "确认接线",
                "config": {"diagram": "placeholder"},
            }
        ],
    )
    run_response = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [source["id"]],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()

    with SessionLocal() as db:
        now = beijing_now()
        db.add(
            ResourceLock(
                resource_id=source["id"],
                run_id=run["id"],
                acquired_at=now,
                lease_expires_at=now + timedelta(minutes=30),
                released_at=None,
                release_reason=None,
            )
        )
        db.commit()

    response = client.post(f"/api/v1/resources/{source['id']}/copy", headers=admin_headers)
    assert response.status_code == 201, response.text
    copied_id = response.json()["id"]

    with SessionLocal() as db:
        relation_types = (
            PlanResource,
            ScenarioResource,
            WorkflowVersionResource,
            RunResource,
            ResourceLock,
        )
        assert all(db.query(link).filter(link.resource_id == source["id"]).count() >= 1 for link in relation_types)
        assert all(db.query(link).filter(link.resource_id == copied_id).count() == 0 for link in relation_types)


def test_copy_resource_rejects_logically_deleted_source(
    client: TestClient,
    admin_headers: dict[str, str],
):
    source = create_resource(client, admin_headers, "REM 已删除复制源")
    create_plan_scenario(client, admin_headers, resource_ids=[source["id"]])

    deleted = client.delete(f"/api/v1/resources/{source['id']}", headers=admin_headers)
    assert deleted.status_code == 204
    copy_deleted = client.post(f"/api/v1/resources/{source['id']}/copy", headers=admin_headers)
    assert copy_deleted.status_code == 404

    db = SessionLocal()
    try:
        deleted_source = db.get(Resource, source["id"])
        assert deleted_source is not None
        assert deleted_source.is_deleted is True
    finally:
        db.close()


def test_copy_resource_is_available_to_each_authenticated_role_and_is_audited(
    client: TestClient,
    admin_headers: dict[str, str],
):
    source = create_resource(client, admin_headers, "REM 角色复制源")
    expected_actor_ids: dict[str, int] = {}
    for username, role in (("copy-visitor", "visitor"), ("copy-tester", "tester")):
        created = client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={"username": username, "display_name": username, "password": "copy-password", "role": role},
        )
        assert created.status_code == 201, created.text
        login = client.post("/api/v1/auth/login", json={"username": username, "password": "copy-password"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        copied = client.post(f"/api/v1/resources/{source['id']}/copy", headers=headers)
        assert copied.status_code == 201, copied.text
        expected_actor_ids[str(copied.json()["id"])] = created.json()["id"]

    copied_by_admin = client.post(f"/api/v1/resources/{source['id']}/copy", headers=admin_headers)
    assert copied_by_admin.status_code == 201, copied_by_admin.text
    anonymous = client.post(f"/api/v1/resources/{source['id']}/copy")
    assert anonymous.status_code == 401
    missing = client.post("/api/v1/resources/999999/copy", headers=admin_headers)
    assert missing.status_code == 404

    db = SessionLocal()
    try:
        admin_id = db.query(User.id).filter(User.username == "admin").scalar()
        assert admin_id is not None
        expected_actor_ids[str(copied_by_admin.json()["id"])] = admin_id
        audits = db.query(AuditLog).filter(AuditLog.action == "resource.copy").all()
        assert {audit.object_id: audit.actor_id for audit in audits} == expected_actor_ids
        assert all(audit.detail == {"source_id": source["id"]} for audit in audits)
    finally:
        db.close()
