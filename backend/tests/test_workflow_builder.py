from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
from sqlalchemy import text

from app.core.database import SessionLocal
from app.models import AuditLog, ConfigurationCaptureSnapshot, ContractDataFile
from app.services import order_configs
from app.services import workflow_capture, workflow_contracts, workflow_publishing, workflows
from conftest import create_plan_scenario, create_resource


def node(key: str, node_type: str, name: str, config: dict) -> dict:
    return {"node_key": key, "node_type": node_type, "name": name, "config": config}


ORDER_XML = '''<?xml version="1.0" encoding="utf-8"?>
<tcp>
  <group_new_order id="new_order" disp="NEW_ORDER"><price disp="PRICE" value="1495.0000" /></group_new_order>
</tcp>'''


class FakeRemoteFile:
    def __init__(self, sftp: "FakeSFTP", path: str) -> None:
        self.sftp = sftp
        self.path = path

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def read(self, _: int) -> str:
        return self.sftp.files[self.path]["content"]

    async def write(self, content: str) -> int:
        self.sftp.files[self.path] = {
            "content": content,
            "permissions": 0o600,
            "mtime": 1_700_000_100,
            "type": asyncssh.FILEXFER_TYPE_REGULAR,
        }
        return len(content)


class FakeSFTP:
    def __init__(self, directory: str) -> None:
        self.files = {
            f"{directory}/ees_ef_vi_trader_api_test_conf.xml": {
                "content": ORDER_XML,
                "permissions": 0o744,
                "mtime": 1_700_000_000,
                "type": asyncssh.FILEXFER_TYPE_REGULAR,
            }
        }
        self.directories = set()

    async def scandir(self, path: str):
        for full_path, item in list(self.files.items()):
            if full_path.rsplit("/", 1)[0] != path:
                continue
            yield SimpleNamespace(
                filename=full_path.rsplit("/", 1)[1],
                attrs=asyncssh.SFTPAttrs(
                    type=item["type"],
                    size=len(item["content"].encode()),
                    permissions=item["permissions"],
                    mtime=item["mtime"],
                ),
            )

    async def lstat(self, path: str):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("missing")
        item = self.files[path]
        return asyncssh.SFTPAttrs(
            type=item["type"],
            size=len(item["content"].encode()),
            permissions=item["permissions"],
            mtime=item["mtime"],
        )

    def open(self, path: str, mode: str, **_):
        return FakeRemoteFile(self, path)

    async def exists(self, path: str) -> bool:
        return path in self.files

    async def setstat(self, path: str, attrs):
        self.files[path]["permissions"] = attrs.permissions

    async def rename(self, old: str, new: str):
        self.files[new] = self.files.pop(old)

    async def posix_rename(self, old: str, new: str):
        self.files[new] = self.files.pop(old)

    async def put(self, local_path: str, remote_path: str):
        self.files[remote_path] = {
            "content": Path(local_path).read_text(encoding="utf-8-sig"),
            "permissions": 0o600,
            "mtime": 1_700_000_200,
            "type": asyncssh.FILEXFER_TYPE_REGULAR,
        }

    async def get(self, remote_path: str, local_path: str):
        Path(local_path).write_text(self.files[remote_path]["content"], encoding="utf-8")

    async def remove(self, path: str):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("missing")
        del self.files[path]

    async def makedirs(self, path: str, exist_ok: bool = False):
        self.directories.add(path)

    def exit(self):
        return None


class FakeConnection:
    def __init__(self, sftp: FakeSFTP) -> None:
        self.sftp = sftp

    async def start_sftp_client(self):
        return self.sftp

    def close(self):
        return None

    async def wait_closed(self):
        return None


class FakeCaptureConnection:
    def __init__(self, fail_cpu: bool = False) -> None:
        self.fail_cpu = fail_cpu

    async def run(self, command, check=False):
        if "lscpu" in command and self.fail_cpu:
            return SimpleNamespace(exit_status=1, stdout="", stderr="cpu read failed")
        if "ip -o -4 addr show" in command:
            return SimpleNamespace(exit_status=0, stdout="10.0.0.1/24\n", stderr="")
        return SimpleNamespace(exit_status=0, stdout="captured\n", stderr="")

    def close(self):
        return None

    async def wait_closed(self):
        return None


def create_order_resource(client, headers) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": "Order-01", "resource_type": "order", "business_code": "fut_mm",
        "host": "127.0.0.1", "ssh_port": 22, "username": "tester",
        "auth_type": "password", "password": "secret",
        "remote_path": "/home/tester/ees_ef_vi_trader_binary_api_test",
        "capabilities": {"order_tool": "ees_ef_vi_trader_binary_api_test"},
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_database_resource(client, headers) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": "Database-01", "resource_type": "database", "business_code": "fut_mm",
        "host": "", "ssh_port": 22, "username": "", "auth_type": "password",
        "database_engine": "mysql", "database_connection_mode": "direct",
        "database_host": "127.0.0.1", "database_port": 3306,
        "database_names": ["alpha_config", "alpha_trading_data"],
        "database_username": "tester", "database_password": "secret",
        "database_tls_enabled": False, "remote_path": "", "capabilities": {},
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_runtime_order_config_can_switch_xml_and_interface_before_start(
    client, admin_headers, monkeypatch
):
    order = create_order_resource(client, admin_headers)
    directory = order["remote_path"]
    sftp = FakeSFTP(directory)
    alternate_name = "ees_ef_vi_trader_api_test_conf-runtime.xml"
    alternate_xml = ORDER_XML.replace('value="1495.0000"', 'value="1501.2500"')
    sftp.files[f"{directory}/{alternate_name}"] = {
        "content": alternate_xml,
        "permissions": 0o744,
        "mtime": 1_700_000_300,
        "type": asyncssh.FILEXFER_TYPE_REGULAR,
    }

    async def fake_connect(**_options):
        return FakeConnection(sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflow_contracts.asyncssh, "connect", fake_connect)
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[order["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    source_name = "ees_ef_vi_trader_api_test_conf.xml"
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [order["id"]],
            "nodes": [{
                "node_key": "order",
                "node_type": "order_preparation",
                "name": "发单执行",
                "config": {
                    "xml_filename": source_name,
                    "network_interface": "p4p1",
                    "read_symbol_csv": 0,
                },
            }],
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish",
        headers=admin_headers,
    )
    assert published.status_code == 200, published.text
    created = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [order["id"]],
        },
    ).json()
    assert client.post(
        f"/api/v1/runs/{created['id']}/start", headers=admin_headers
    ).status_code == 200
    step_id = created["steps"][0]["id"]

    updated = client.put(
        f"/api/v1/runs/{created['id']}/steps/{step_id}/order-config",
        headers=admin_headers,
        json={
            "xml_filename": alternate_name,
            "network_interface": "enp1s0",
        },
    )

    assert updated.status_code == 200, updated.text
    step = updated.json()["steps"][0]
    assert step["config_snapshot"]["xml_filename"] == alternate_name
    assert step["config_snapshot"]["network_interface"] == "enp1s0"
    assert step["config_snapshot"]["xml_checksum"] == hashlib.sha256(
        alternate_xml.encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_prepare_order_node_uses_runtime_snapshot_and_latest_xml(
    monkeypatch,
):
    requested = []
    latest_xml = ORDER_XML.replace('value="1495.0000"', 'value="1508.5000"')

    async def read_latest(_resource, filename):
        requested.append(filename)
        return {
            "name": filename,
            "content": latest_xml,
            "checksum": "f" * 64,
            "document": {"type": "element", "name": "tcp", "attributes": [], "children": []},
        }

    monkeypatch.setattr(workflow_contracts.order_config_service, "read", read_latest)
    node_record = SimpleNamespace(
        node_type="order_preparation",
        config={
            "xml_filename": "ees_ef_vi_trader_api_test_conf-published.xml",
            "xml_checksum": "a" * 64,
            "network_interface": "p4p1",
            "read_symbol_csv": 0,
        },
        contract_file_links=[],
    )
    step = SimpleNamespace(config_snapshot={
        "xml_filename": "ees_ef_vi_trader_api_test_conf-runtime.xml",
        "xml_checksum": "b" * 64,
        "network_interface": "enp1s0",
        "read_symbol_csv": 0,
        "contract_file_ids": [],
        "contract_files": [],
    })
    resource = SimpleNamespace(
        remote_path="/tmp/ees_ef_vi_trader_binary_api_test",
        capabilities={"order_tool": "ees_ef_vi_trader_binary_api_test"},
    )

    result = await workflow_contracts.prepare_order_node(
        None,
        SimpleNamespace(),
        node_record,
        {"order": resource},
        step=step,
    )

    assert requested == ["ees_ef_vi_trader_api_test_conf-runtime.xml"]
    assert result["xml_checksum"] == "f" * 64
    assert result["network_interface"] == "enp1s0"
    assert "ZF_ATTR=interface=enp1s0" in result["generated_command"]


def test_workflow_draft_revision_preview_and_publish(client, admin_headers, monkeypatch):
    async def fake_connect(**_options):
        return FakeCaptureConnection()

    monkeypatch.setattr(workflow_capture.asyncssh, "connect", fake_connect)
    rem = create_resource(client, admin_headers, "REM-01")
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    document = client.get(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers).json()
    payload = {
        "expected_revision": document["draft"]["revision"],
        "resource_ids": [rem["id"]],
        "nodes": [{
            "node_key": "server-config", "node_type": "server_config", "name": "采集服务器配置",
            "config": {"targets": [{"resource_type": "rem", "fields": ["ip", "nic_model", "machine_model", "os_version", "cpu_model"]}]},
        }],
    }
    saved = client.put(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"]["revision"] == payload["expected_revision"] + 1
    conflict = client.put(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers, json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "WORKFLOW_REVISION_CONFLICT"

    preview = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/server-config/preview", headers=admin_headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()[0]["status"] == "succeeded"
    assert len(preview.json()[0]["items"]) == 5

    published = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    assert published.json()["draft"]["status"] == "published"
    listed = client.get("/api/v1/scenarios", headers=admin_headers).json()
    assert listed[0]["is_enabled"] is True
    assert listed[0]["workflow_status"] == "published"


def test_database_preview_releases_platform_write_lock_during_remote_read(
    client,
    admin_headers,
    monkeypatch,
):
    database = create_database_resource(client, admin_headers)
    _, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[database["id"]],
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [database["id"]],
            "nodes": [node(
                "database-config",
                "database_config",
                "采集数据库配置",
                {"database_name": "alpha_config", "keys": ["SETTING_A"]},
            )],
        },
    )
    assert saved.status_code == 200, saved.text

    class FakeCursor:
        def __init__(self) -> None:
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _parameters=None):
            self.rows = (
                [("setting_key",), ("setting_value",), ("description",)]
                if statement.startswith("SHOW COLUMNS")
                else [("SETTING_A", "captured", "采集配置说明")]
            )

        def fetchall(self):
            return self.rows

    class FakeDatabaseConnection:
        def cursor(self):
            return FakeCursor()

    @asynccontextmanager
    async def fake_connection(_resource, _database_name):
        # This is the point where the real adapter begins remote I/O. A second platform write
        # must succeed immediately instead of waiting for the preview transaction to finish.
        with SessionLocal() as parallel_db:
            parallel_db.execute(text("PRAGMA busy_timeout = 100"))
            parallel_db.add(AuditLog(
                action="test.concurrent_write",
                object_type="test",
                trace_id="test-concurrent-write",
            ))
            parallel_db.commit()
        yield FakeDatabaseConnection()

    monkeypatch.setattr(workflow_capture.mysql_adapter, "connection", fake_connection)
    preview = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/database-config/preview",
        headers=admin_headers,
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()[0]["status"] == "succeeded"
    assert preview.json()[0]["items"][0]["value_text"] == "captured"
    assert preview.json()[0]["items"][0]["item_description"] == "采集配置说明"
    with SessionLocal() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "test.concurrent_write").count() == 1


def test_unpublished_scenario_cannot_run(client, admin_headers):
    rem = create_resource(client, admin_headers, "REM-01")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    response = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem["id"]], "timeout_minutes": 30,
    })
    assert response.status_code == 404


def test_pause_edits_same_visible_version_without_affecting_existing_run(client, admin_headers):
    rem = create_resource(client, admin_headers, "REM-version-snapshot")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [rem["id"]],
            "nodes": [node("wiring-v1", "wiring_confirmation", "旧接线确认", {"diagram": "placeholder"})],
        },
    )
    assert saved.status_code == 200, saved.text
    enabled = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/enable", headers=admin_headers
    )
    assert enabled.status_code == 200, enabled.text
    published_id = enabled.json()["draft"]["id"]

    existing = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem["id"]]},
    )
    assert existing.status_code == 201, existing.text
    assert existing.json()["steps"][0]["name"] == "旧接线确认"

    paused = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/pause", headers=admin_headers
    )
    assert paused.status_code == 200, paused.text
    paused_document = paused.json()
    assert paused_document["draft"]["version_no"] == 1
    assert paused_document["draft"]["id"] != published_id
    assert paused_document["scenario"]["is_enabled"] is False

    draft_node = paused_document["draft"]["nodes"][0]
    updated = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": paused_document["draft"]["revision"],
            "resource_ids": [rem["id"]],
            "nodes": [node(draft_node["node_key"], "wiring_confirmation", "新接线确认", {"diagram": "placeholder"})],
        },
    )
    assert updated.status_code == 200, updated.text
    blocked = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem["id"]]},
    )
    assert blocked.status_code == 404

    run_id = existing.json()["id"]
    assert client.post(f"/api/v1/runs/{run_id}/start", headers=admin_headers).status_code == 200
    old_step = client.get(f"/api/v1/runs/{run_id}", headers=admin_headers).json()["steps"][0]
    started = client.post(
        f"/api/v1/runs/{run_id}/steps/{old_step['id']}/start", headers=admin_headers
    )
    assert started.status_code == 200, started.text

    reenabled = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/enable", headers=admin_headers
    )
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["draft"]["version_no"] == 1
    versions = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions", headers=admin_headers
    ).json()
    assert [(item["version_no"], item["status"]) for item in versions] == [(1, "published")]
    replacement = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem["id"]]},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["steps"][0]["name"] == "新接线确认"


def test_create_and_archive_workflow_versions(client, admin_headers):
    rem = create_resource(client, admin_headers, "REM-version-archive")
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [rem["id"]],
            "nodes": [node("wiring-v1", "wiring_confirmation", "接线确认", {"diagram": "placeholder"})],
        },
    )
    assert saved.status_code == 200, saved.text
    enabled = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/enable", headers=admin_headers
    ).json()

    created = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions",
        headers=admin_headers,
        json={"source_version_id": enabled["draft"]["id"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["draft"]["version_no"] == 2
    duplicate = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions",
        headers=admin_headers,
        json={"source_version_id": enabled["draft"]["id"]},
    )
    assert duplicate.status_code == 409

    archived = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions/{created.json()['draft']['id']}/archive",
        headers=admin_headers,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    visible = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions", headers=admin_headers
    ).json()
    assert [item["version_no"] for item in visible] == [1]
    all_versions = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions?include_archived=true",
        headers=admin_headers,
    ).json()
    assert [(item["version_no"], item["status"]) for item in all_versions] == [
        (2, "archived"),
        (1, "retired"),
    ]

    resumed = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/enable", headers=admin_headers
    )
    assert resumed.status_code == 200, resumed.text
    reject = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/versions/{enabled['draft']['id']}/archive",
        headers=admin_headers,
    )
    assert reject.status_code == 409


def test_scan_existing_contract_csv_files(client, admin_headers, monkeypatch):
    order = create_order_resource(client, admin_headers)
    sftp = FakeSFTP(order["remote_path"])
    sftp.files[f'{order["remote_path"]}/t_close_report_20260728.csv'] = {
        "content": "quote_date,symbol\n2026-07-28,IF2608\n2026-07-28,IF2609\n",
        "permissions": 0o600,
        "mtime": 1_700_000_300,
        "type": asyncssh.FILEXFER_TYPE_REGULAR,
    }
    sftp.files[f'{order["remote_path"]}/t_close_report_opt_20260728.csv'] = {
        "content": "quote_date,symbol\n2026-07-28,IO2608-C-4000\n",
        "permissions": 0o600,
        "mtime": 1_700_000_301,
        "type": asyncssh.FILEXFER_TYPE_REGULAR,
    }
    sftp.files[f'{order["remote_path"]}/notes.txt'] = {
        "content": "ignored",
        "permissions": 0o600,
        "mtime": 1_700_000_302,
        "type": asyncssh.FILEXFER_TYPE_REGULAR,
    }

    async def fake_connect(**_options):
        return FakeConnection(sftp)

    monkeypatch.setattr(workflow_contracts.asyncssh, "connect", fake_connect)
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[order["id"]])
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [order["id"]],
            "nodes": [{
                "node_key": "order",
                "node_type": "order_preparation",
                "name": "Prepare order",
                "config": {"xml_filename": "", "read_symbol_csv": 0},
            }],
        },
    )
    assert saved.status_code == 200, saved.text

    endpoint = f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/order/contract-files/scan"
    scanned = client.post(endpoint, headers=admin_headers)
    assert scanned.status_code == 200, scanned.text
    files = scanned.json()
    assert {item["filename"] for item in files} == {
        "t_close_report_20260728.csv",
        "t_close_report_opt_20260728.csv",
    }
    assert {item["contract_type"] for item in files} == {"futures", "options"}
    assert {item["row_count"] for item in files} == {1, 2}
    assert all(item["quote_date"] == "2026-07-28" for item in files)
    assert all(item["database_resource_id"] is None for item in files)
    assert len(next(item for item in files if item["contract_type"] == "futures")["preview_rows"]) == 2

    repeated = client.post(endpoint, headers=admin_headers)
    assert repeated.status_code == 200, repeated.text
    assert {item["id"] for item in repeated.json()} == {item["id"] for item in files}

    _, second_scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[order["id"]]
    )
    second_document = client.get(
        f"/api/v1/scenarios/{second_scenario['id']}/workflow", headers=admin_headers
    ).json()
    second_saved = client.put(
        f"/api/v1/scenarios/{second_scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": second_document["draft"]["revision"],
            "resource_ids": [order["id"]],
            "nodes": [{
                "node_key": "order",
                "node_type": "order_preparation",
                "name": "Prepare order",
                "config": {"xml_filename": "", "read_symbol_csv": 0},
            }],
        },
    )
    assert second_saved.status_code == 200, second_saved.text
    second_scan = client.post(
        f"/api/v1/scenarios/{second_scenario['id']}/workflow/nodes/order/contract-files/scan",
        headers=admin_headers,
    )
    assert second_scan.status_code == 200, second_scan.text
    assert {item["id"] for item in second_scan.json()} == {item["id"] for item in files}
    with SessionLocal() as db:
        assert db.query(ContractDataFile).count() == 2


def test_publish_rejects_incomplete_order_node(client, admin_headers):
    order = create_resource(client, admin_headers, "Order-01", resource_type="order")
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[order["id"]])
    document = client.get(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers).json()
    saved = client.put(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers, json={
        "expected_revision": document["draft"]["revision"], "resource_ids": [order["id"]],
        "nodes": [{"node_key": "order", "node_type": "order_preparation", "name": "发单准备", "config": {"xml_filename": "", "network_interface": "bad interface", "read_symbol_csv": 0}}],
    })
    assert saved.status_code == 200
    published = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert published.status_code == 422
    assert published.json()["code"] == "WORKFLOW_VALIDATION_FAILED"


def test_publish_rejects_database_config_key_removed_from_catalog(
    client,
    admin_headers,
    monkeypatch,
):
    async def fake_database_config_items(_resource, _database_name):
        return [{"key": "CURRENT_KEY", "description": "仍存在"}]

    monkeypatch.setattr(
        workflow_publishing,
        "list_database_config_items",
        fake_database_config_items,
    )
    database = create_database_resource(client, admin_headers)
    _, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[database["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [database["id"]],
            "nodes": [{
                "node_key": "database",
                "node_type": "database_config",
                "name": "数据库配置",
                "config": {"database_name": "alpha_config", "keys": ["REMOVED_KEY"]},
            }],
        },
    )
    assert saved.status_code == 200, saved.text
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 422
    assert published.json()["errors"] == [{
        "node_key": "database",
        "field": "keys",
        "message": "配置项已不存在：REMOVED_KEY",
    }]


def test_contract_fetch_selection_publish_and_run_snapshot(client, admin_headers, monkeypatch):
    order = create_order_resource(client, admin_headers)
    database = create_database_resource(client, admin_headers)
    sftp = FakeSFTP(order["remote_path"])

    async def fake_connect(**_options):
        return FakeConnection(sftp)

    async def fake_export_contract_csv(database_resource, database_name, table, target):
        quote_date = "2026-07-20"
        rows = [
            {"quote_date": quote_date, "symbol": f"TEST{index:04d}", "exchange": "SIM"}
            for index in range(1, 7)
        ]
        target.write_text(
            "quote_date,symbol,exchange\n"
            + "\n".join(f"{row['quote_date']},{row['symbol']},{row['exchange']}" for row in rows)
            + "\n",
            encoding="utf-8-sig",
        )
        return quote_date, len(rows), rows[:5]

    async def fake_database_config_items(_resource, database_name):
        assert database_name == "alpha_config"
        return [{"key": "CLIENT_REQ_BIND_CPU", "description": "客户端请求绑核"}]

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflow_contracts.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(workflow_contracts, "_export_contract_csv", fake_export_contract_csv)
    monkeypatch.setattr(
        workflow_publishing,
        "list_database_config_items",
        fake_database_config_items,
    )
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[database["id"], order["id"]]
    )
    xml_base = f"/api/v1/resources/{order['id']}/order-configs"
    listed = client.get(xml_base, headers=admin_headers).json()
    xml_filename = listed["files"][0]["name"]
    detail = client.get(f"{xml_base}/{xml_filename}", headers=admin_headers).json()
    xml_with_contracts = detail["content"].replace(
        "</tcp>",
        '<read_symbol_csv value="1" />\n'
        '<fut_symbol_csv value="t_close_report.csv" />\n'
        '<opt_symbol_csv value="t_close_report_opt.csv" />\n'
        '</tcp>',
    )
    updated = client.put(
        f"{xml_base}/{xml_filename}", headers=admin_headers,
        json={"content": xml_with_contracts, "expected_checksum": detail["checksum"]},
    )
    assert updated.status_code == 200, updated.text

    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    nodes = [
        {
            "node_key": "database", "node_type": "database_config", "name": "Capture database",
            "config": {"database_name": "alpha_config", "keys": ["CLIENT_REQ_BIND_CPU"]},
        },
        {
            "node_key": "order", "node_type": "order_preparation", "name": "Prepare order",
            "config": {
                "xml_filename": xml_filename, "network_interface": "p4p1",
                "read_symbol_csv": 0, "trading_database_name": "alpha_trading_data",
                "contract_file_ids": [],
            },
        },
    ]
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [database["id"], order["id"]], "nodes": nodes,
        },
    )
    assert saved.status_code == 200, saved.text

    fetched = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/order/contract-files/fetch",
        headers=admin_headers,
        json={
            "database_resource_id": database["id"],
            "database_name": "alpha_trading_data",
            "contract_types": ["futures", "options"],
        },
    )
    assert fetched.status_code == 201, fetched.text
    files = fetched.json()
    assert {item["source_table"] for item in files} == {"t_close_report", "t_close_report_opt"}
    assert all(item["row_count"] == 6 for item in files)
    assert all(len(item["preview_rows"]) == 5 for item in files)
    assert all(len(item["checksum"]) == 64 and item["quote_date"] for item in files)
    repeated = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/order/contract-files/fetch",
        headers=admin_headers,
        json={
            "database_resource_id": database["id"],
            "database_name": "alpha_trading_data",
            "contract_types": ["futures", "options"],
        },
    )
    assert repeated.status_code == 201
    assert {item["id"] for item in repeated.json()} == {item["id"] for item in files}

    latest = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    for item in nodes:
        if item["node_key"] == "order":
            item["config"]["contract_file_ids"] = [contract["id"] for contract in files]
    resaved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers,
        json={
            "expected_revision": latest["draft"]["revision"],
            "resource_ids": [database["id"], order["id"]], "nodes": nodes,
        },
    )
    assert resaved.status_code == 200, resaved.text
    listed_files = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow/nodes/order/contract-files",
        headers=admin_headers,
    ).json()
    assert {item["id"] for item in listed_files} == {item["id"] for item in files}

    with SessionLocal() as db:
        stored = db.get(ContractDataFile, files[0]["id"])
        archive = Path(stored.archive_path)
        original = archive.read_bytes()
        archive.write_bytes(original + b"drift")
    drifted = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert drifted.status_code == 422
    assert drifted.json()["code"] == "WORKFLOW_VALIDATION_FAILED"
    archive.write_bytes(original)

    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 200, published.text
    published_order = next(
        item for item in published.json()["draft"]["nodes"] if item["node_key"] == "order"
    )
    assert published_order["config"]["read_symbol_csv"] == 1
    published_xml = client.get(f"{xml_base}/{xml_filename}", headers=admin_headers).json()
    expected_symbol_files = {
        "fut_symbol_csv": next(item["filename"] for item in files if item["contract_type"] == "futures"),
        "opt_symbol_csv": next(item["filename"] for item in files if item["contract_type"] == "options"),
    }
    symbol_values = {
        child["name"]: {item["name"]: item["value"] for item in child["attributes"]}["value"]
        for child in published_xml["document"]["children"]
        if child["type"] == "element" and child["name"] in expected_symbol_files
    }
    assert symbol_values == expected_symbol_files
    assert published_order["config"]["xml_checksum"] == published_xml["checksum"]
    assert published_xml["checksum"] != updated.json()["checksum"]

    run = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"],
        "resource_ids": [database["id"], order["id"]], "timeout_minutes": 30,
    })
    assert run.status_code == 201, run.text
    order_snapshot = next(
        item for item in run.json()["config_snapshot"]["workflow"]["nodes"]
        if item["node_key"] == "order"
    )["config"]
    assert {item["checksum"] for item in order_snapshot["contract_files"]} == {
        item["checksum"] for item in files
    }
    order_step = next(item for item in run.json()["steps"] if item["code"] == "order")
    assert order_step["config_snapshot"]["contract_files"] == order_snapshot["contract_files"]

    cloned = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/draft", headers=admin_headers
    )
    assert cloned.status_code == 200, cloned.text
    cloned_order = next(
        item for item in cloned.json()["draft"]["nodes"] if item["node_type"] == "order_preparation"
    )
    assert set(cloned_order["config"]["contract_file_ids"]) == {item["id"] for item in files}
    with SessionLocal() as db:
        assert db.query(ContractDataFile).count() == 2
    republished = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert republished.status_code == 200, republished.text
    assert republished.json()["published_version_id"] != run.json()["workflow_version_id"]
    historical_run = client.get(
        f"/api/v1/runs/{run.json()['id']}", headers=admin_headers
    ).json()
    assert historical_run["workflow_version_id"] == run.json()["workflow_version_id"]

    copied = client.post(f"/api/v1/scenarios/{scenario['id']}/copy", headers=admin_headers)
    assert copied.status_code == 201, copied.text
    copied_document = client.get(
        f"/api/v1/scenarios/{copied.json()['id']}/workflow", headers=admin_headers
    ).json()
    copied_order = next(
        item for item in copied_document["draft"]["nodes"]
        if item["node_type"] == "order_preparation"
    )
    assert set(copied_order["config"]["contract_file_ids"]) == {item["id"] for item in files}
    with SessionLocal() as db:
        assert db.query(ContractDataFile).count() == 2


def test_capture_failure_saves_partial_results_and_retry_attempt(client, admin_headers, monkeypatch):
    rem = create_resource(client, admin_headers, "REM-retry")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"], "resource_ids": [rem["id"]],
            "nodes": [{
                "node_key": "capture", "node_type": "server_config", "name": "Capture REM",
                "config": {"targets": [{"resource_type": "rem", "fields": ["ip", "cpu_model"]}]},
            }],
        },
    )
    assert saved.status_code == 200
    assert client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    ).status_code == 200
    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"],
        "resource_ids": [rem["id"]], "timeout_minutes": 30,
    }).json()

    attempts = {"count": 0}

    async def fake_connect(**_options):
        attempts["count"] += 1
        return FakeCaptureConnection(fail_cpu=attempts["count"] == 1)

    monkeypatch.setattr(workflow_capture.asyncssh, "connect", fake_connect)
    assert client.post(f"/api/v1/runs/{created['id']}/start", headers=admin_headers).status_code == 200
    step_id = created["steps"][0]["id"]
    assert client.post(
        f"/api/v1/runs/{created['id']}/steps/{step_id}/start",
        headers=admin_headers,
    ).status_code == 200
    failed = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
    assert failed["status"] == "awaiting_step_retry"
    assert failed["steps"][0]["result_summary"] == {"snapshot_ids": [1], "sources": 1, "failed": 1}

    retried = client.post(f"/api/v1/runs/{created['id']}/retry", headers=admin_headers)
    assert retried.status_code == 200, retried.text
    waiting = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
    assert waiting["status"] == "awaiting_step_completion"
    assert client.post(
        f"/api/v1/runs/{created['id']}/steps/{step_id}/complete",
        headers=admin_headers,
    ).status_code == 200
    completed = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["retry_count"] == 1
    with SessionLocal() as db:
        snapshots = db.query(ConfigurationCaptureSnapshot).order_by(
            ConfigurationCaptureSnapshot.id
        ).all()
        assert [item.attempt for item in snapshots] == [1, 2]
        assert [entry.status for entry in snapshots[0].items] == ["succeeded", "failed"]
        assert all(entry.status == "succeeded" for entry in snapshots[1].items)
