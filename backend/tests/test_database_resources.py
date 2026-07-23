from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from app.adapters.database import DatabaseDiscoveryConfig, mysql_adapter, parse_select, parse_update
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AuditLog


def database_payload(**overrides):
    payload = {
        "name": "REM 业务库",
        "resource_type": "database",
        "business_code": "rem_two",
        "database_engine": "mysql",
        "database_connection_mode": "direct",
        "database_host": "10.0.0.8",
        "database_port": 3306,
        "database_names": [" rem_core ", "rem_report", "rem_core"],
        "database_username": "rem_reader",
        "database_password": "db-secret",
        "database_tls_enabled": True,
        "capabilities": {},
        "version_info": "",
        "notes": "",
        "is_enabled": True,
    }
    payload.update(overrides)
    return payload


def create_database(client: TestClient, headers: dict[str, str], **overrides) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json=database_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_database_resource_normalizes_names_and_hides_secret(client: TestClient, admin_headers: dict[str, str]):
    resource = create_database(client, admin_headers)
    assert resource["database_names"] == ["rem_core", "rem_report"]
    assert resource["database_connection_mode"] == "direct"
    assert resource["database_tls_enabled"] is True
    assert resource["has_database_password"] is True
    assert "database_password" not in resource
    assert "encrypted_database_password" not in resource

    edited = client.put(
        f"/api/v1/resources/{resource['id']}",
        headers=admin_headers,
        json=database_payload(database_password="", notes="keep password"),
    )
    assert edited.status_code == 200
    assert edited.json()["has_database_password"] is True


def test_tunnel_requires_ssh_endpoint(client: TestClient, admin_headers: dict[str, str]):
    invalid = client.post(
        "/api/v1/resources",
        headers=admin_headers,
        json=database_payload(database_connection_mode="ssh_tunnel"),
    )
    assert invalid.status_code == 422

    resource = create_database(
        client,
        admin_headers,
        database_connection_mode="ssh_tunnel",
        host="jump.example.test",
        ssh_port=2222,
        username="jump-user",
        auth_type="password",
        password="jump-secret",
    )
    assert resource["host"] == "jump.example.test"
    assert resource["database_host"] == "10.0.0.8"


def test_simulated_health_select_export_and_update(client: TestClient, admin_headers: dict[str, str]):
    resource = create_database(client, admin_headers)
    resource_id = resource["id"]
    health = client.post(f"/api/v1/resources/{resource_id}/health", headers=admin_headers)
    assert health.status_code == 200
    assert health.json()["simulated"] is True
    assert {item["database"] for item in health.json()["details"]} == {"rem_core", "rem_report"}

    query = {"database_name": "rem_core", "sql": "SELECT id, account_name FROM accounts"}
    selected = client.post(f"/api/v1/resources/{resource_id}/database/select", headers=admin_headers, json=query)
    assert selected.status_code == 200, selected.text
    assert selected.json()["simulated"] is True
    assert selected.json()["columns"] == ["id", "account_name"]
    assert selected.json()["rows"]

    csv_response = client.post(
        f"/api/v1/resources/{resource_id}/database/export",
        headers=admin_headers,
        json={**query, "format": "csv"},
    )
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    xlsx_response = client.post(
        f"/api/v1/resources/{resource_id}/database/export",
        headers=admin_headers,
        json={**query, "format": "xlsx"},
    )
    assert xlsx_response.status_code == 200
    assert xlsx_response.content.startswith(b"PK")

    update_sql = "UPDATE accounts SET enabled = 0 WHERE id = 7"
    preview = client.post(
        f"/api/v1/resources/{resource_id}/database/update-preview",
        headers=admin_headers,
        json={"database_name": "rem_core", "sql": update_sql},
    )
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()
    assert 1 <= preview_data["estimated_rows"] <= 5
    assert preview_data["simulated"] is True

    execute_payload = {
        "database_name": "rem_core",
        "sql": update_sql,
        "confirmation_id": preview_data["confirmation_id"],
        "confirmation_text": resource["name"],
    }
    tampered = client.post(
        f"/api/v1/resources/{resource_id}/database/update-execute",
        headers=admin_headers,
        json={**execute_payload, "sql": "UPDATE accounts SET enabled = 1 WHERE id = 7"},
    )
    assert tampered.status_code == 409
    wrong_name = client.post(
        f"/api/v1/resources/{resource_id}/database/update-execute",
        headers=admin_headers,
        json={**execute_payload, "confirmation_text": "wrong resource"},
    )
    assert wrong_name.status_code == 400
    executed = client.post(
        f"/api/v1/resources/{resource_id}/database/update-execute",
        headers=admin_headers,
        json=execute_payload,
    )
    assert executed.status_code == 200
    assert executed.json()["affected_rows"] == preview_data["estimated_rows"]
    replay = client.post(
        f"/api/v1/resources/{resource_id}/database/update-execute",
        headers=admin_headers,
        json=execute_payload,
    )
    assert replay.status_code == 409


def test_visitor_cannot_use_database_console(client: TestClient, admin_headers: dict[str, str]):
    resource = create_database(client, admin_headers)
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "db-viewer", "display_name": "数据库访客", "password": "viewer-password", "role": "visitor"},
    )
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={"username": "db-viewer", "password": "viewer-password"})
    visitor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = client.post(
        f"/api/v1/resources/{resource['id']}/database/select",
        headers=visitor_headers,
        json={"database_name": "rem_core", "sql": "SELECT 1"},
    )
    assert response.status_code == 403


def test_sql_safety_rejects_cross_database_and_unsafe_updates():
    select_plan = parse_select("SELECT id FROM allowed.accounts", "allowed")
    assert select_plan.fingerprint

    for sql in (
        "UPDATE accounts SET enabled=0",
        "UPDATE accounts a JOIN flags f ON a.id=f.id SET a.enabled=0 WHERE f.active=1",
        "UPDATE accounts SET enabled=(SELECT enabled FROM backup LIMIT 1) WHERE id=1",
        "DELETE FROM accounts WHERE id=1",
    ):
        try:
            parse_update(sql, "allowed")
        except Exception:
            pass
        else:
            raise AssertionError(f"unsafe SQL was accepted: {sql}")

    try:
        parse_select("SELECT id FROM forbidden.accounts", "allowed")
    except Exception:
        pass
    else:
        raise AssertionError("cross-database SELECT was accepted")


def test_real_mode_dispatches_to_database_adapter(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    resource = create_database(client, admin_headers)

    class FakeAdapter:
        async def health(self, configured_resource):
            assert configured_resource.id == resource["id"]
            return {"ok": True, "message": "数据库连接成功", "details": [], "version": "5.7.44", "simulated": False}

        async def select(self, configured_resource, database_name, plan):
            assert database_name == "rem_core"
            assert plan.fingerprint
            return {"columns": ["version"], "rows": [{"version": "5.7.44"}], "row_count": 1, "truncated": False, "elapsed_ms": 1, "simulated": False}

    router_module = importlib.import_module("app.api.router")
    monkeypatch.setattr(router_module, "mysql_adapter", FakeAdapter())
    monkeypatch.setattr(settings, "execution_mode", "remote")

    health = client.post(f"/api/v1/resources/{resource['id']}/health", headers=admin_headers)
    assert health.status_code == 200
    assert health.json()["version"] == "5.7.44"
    selected = client.post(
        f"/api/v1/resources/{resource['id']}/database/select",
        headers=admin_headers,
        json={"database_name": "rem_core", "sql": "SELECT VERSION() AS version"},
    )
    assert selected.status_code == 200
    assert selected.json()["simulated"] is False


def discovery_payload(**overrides):
    payload = {
        "database_connection_mode": "direct",
        "database_host": "10.0.0.8",
        "database_port": 3306,
        "database_username": "rem_reader",
        "database_password": "db-secret",
        "database_tls_enabled": False,
        "host": "",
        "ssh_port": 22,
        "username": "",
        "auth_type": "password",
        "password": "",
        "private_key": "",
    }
    payload.update(overrides)
    return payload


def test_simulated_database_discovery_filters_system_databases(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
):
    async def unexpected_discovery(_):
        raise AssertionError("simulated discovery must not connect to MySQL")

    router_module = importlib.import_module("app.api.router")
    monkeypatch.setattr(router_module.mysql_adapter, "discover_databases", unexpected_discovery)
    response = client.post(
        "/api/v1/resources/database/discover",
        headers=admin_headers,
        json=discovery_payload(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "databases": ["fut_mm_config", "fut_mm_log_data", "fut_mm_risk_data"],
        "simulated": True,
        "filtered_system_count": 4,
    }
    db = SessionLocal()
    try:
        audit = db.query(AuditLog).filter(AuditLog.action == "database.discover").one()
        serialized = str(audit.detail)
        assert audit.detail["count"] == 3
        assert "fut_mm_config" not in serialized
        assert "mysql" not in serialized
    finally:
        db.close()

    missing = client.post(
        "/api/v1/resources/database/discover",
        headers=admin_headers,
        json=discovery_payload(resource_id=999_999),
    )
    assert missing.status_code == 404


def test_database_discovery_reuses_secrets_only_for_unchanged_identity(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
):
    resource = create_database(
        client,
        admin_headers,
        database_connection_mode="ssh_tunnel",
        host="jump.example.test",
        ssh_port=2222,
        username="jump-user",
        auth_type="private_key",
        private_key="private-key-secret",
    )

    class FakeAdapter:
        async def discover_databases(self, config):
            assert config.database_password == "db-secret"
            assert config.ssh_private_key == "private-key-secret"
            assert config.ssh_password is None
            return ["rem_core", "rem_report"], 4

    router_module = importlib.import_module("app.api.router")
    monkeypatch.setattr(router_module, "mysql_adapter", FakeAdapter())
    monkeypatch.setattr(settings, "execution_mode", "remote")
    payload = discovery_payload(
        resource_id=resource["id"],
        database_connection_mode="ssh_tunnel",
        database_password="",
        host="jump.example.test",
        ssh_port=2222,
        username="jump-user",
        auth_type="private_key",
        private_key="",
    )
    discovered = client.post("/api/v1/resources/database/discover", headers=admin_headers, json=payload)
    assert discovered.status_code == 200
    assert discovered.json()["databases"] == ["rem_core", "rem_report"]

    changed_database = client.post(
        "/api/v1/resources/database/discover",
        headers=admin_headers,
        json={**payload, "database_host": "10.0.0.9"},
    )
    assert changed_database.status_code == 400
    assert changed_database.json()["code"] == "DATABASE_PASSWORD_REQUIRED"

    changed_jump_host = client.post(
        "/api/v1/resources/database/discover",
        headers=admin_headers,
        json={**payload, "host": "new-jump.example.test", "database_password": "new-secret"},
    )
    assert changed_jump_host.status_code == 400
    assert changed_jump_host.json()["code"] == "SSH_PRIVATE_KEY_REQUIRED"


def test_database_discovery_is_admin_only(client: TestClient, admin_headers: dict[str, str]):
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "discovery-viewer",
            "display_name": "发现访客",
            "password": "viewer-password",
            "role": "visitor",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "discovery-viewer", "password": "viewer-password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = client.post(
        "/api/v1/resources/database/discover",
        headers=headers,
        json=discovery_payload(),
    )
    assert response.status_code == 403


async def test_database_adapter_discovers_direct_connection(monkeypatch):
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            assert sql == "SHOW DATABASES"

        def fetchall(self):
            return [
                ("mysql",),
                ("rem_report",),
                ("REM_CORE",),
                ("rem_core",),
                ("information_schema",),
            ]

    class FakeConnection:
        closed = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = True

    connection = FakeConnection()

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return connection

    database_module = importlib.import_module("app.adapters.database")
    monkeypatch.setattr(database_module.pymysql, "connect", fake_connect)
    databases, filtered = await mysql_adapter.discover_databases(
        DatabaseDiscoveryConfig(
            database_host="10.0.0.8",
            database_port=3306,
            database_username="reader",
            database_password="secret",
            database_tls_enabled=False,
            connection_mode="direct",
        )
    )
    assert databases == ["REM_CORE", "rem_report"]
    assert filtered == 2
    assert captured["database"] is None
    assert captured["password"] == "secret"
    assert connection.closed is True


async def test_database_adapter_discovers_through_ssh_tunnel(monkeypatch):
    captured_database = {}
    captured_ssh = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            assert sql == "SHOW DATABASES"

        def fetchall(self):
            return [("rem_core",), ("sys",)]

    class FakeDatabaseConnection:
        closed = False

        def cursor(self):
            return FakeCursor()

        def close(self):
            self.closed = True

    class FakeTunnel:
        closed = False
        waited = False

        def get_port(self):
            return 43127

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    class FakeSSHConnection:
        closed = False
        waited = False

        async def forward_local_port(self, local_host, local_port, remote_host, remote_port):
            assert (local_host, local_port) == ("127.0.0.1", 0)
            assert (remote_host, remote_port) == ("10.0.0.8", 3306)
            return tunnel

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    database_connection = FakeDatabaseConnection()
    tunnel = FakeTunnel()
    ssh_connection = FakeSSHConnection()

    def fake_database_connect(**kwargs):
        captured_database.update(kwargs)
        return database_connection

    async def fake_ssh_connect(**kwargs):
        captured_ssh.update(kwargs)
        return ssh_connection

    database_module = importlib.import_module("app.adapters.database")
    monkeypatch.setattr(database_module.pymysql, "connect", fake_database_connect)
    monkeypatch.setattr(database_module.asyncssh, "connect", fake_ssh_connect)
    databases, filtered = await mysql_adapter.discover_databases(
        DatabaseDiscoveryConfig(
            database_host="10.0.0.8",
            database_port=3306,
            database_username="reader",
            database_password="db-secret",
            database_tls_enabled=False,
            connection_mode="ssh_tunnel",
            ssh_host="jump.example.test",
            ssh_port=2222,
            ssh_username="jump-user",
            ssh_password="jump-secret",
        )
    )
    assert databases == ["rem_core"]
    assert filtered == 1
    assert captured_ssh["host"] == "jump.example.test"
    assert captured_ssh["password"] == "jump-secret"
    assert captured_database["host"] == "127.0.0.1"
    assert captured_database["port"] == 43127
    assert database_connection.closed
    assert tunnel.closed and tunnel.waited
    assert ssh_connection.closed and ssh_connection.waited
