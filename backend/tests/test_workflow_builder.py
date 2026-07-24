from __future__ import annotations

from pathlib import Path

from app.core.database import SessionLocal
from app.models import ContractDataFile
from app.services import workflows
from conftest import create_plan_scenario, create_resource


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


def test_workflow_draft_revision_preview_and_publish(client, admin_headers):
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


def test_unpublished_scenario_cannot_run(client, admin_headers):
    rem = create_resource(client, admin_headers, "REM-01")
    plan, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    response = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem["id"]], "timeout_minutes": 30,
    })
    assert response.status_code == 404


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


def test_contract_fetch_selection_publish_and_run_snapshot(client, admin_headers):
    order = create_order_resource(client, admin_headers)
    database = create_database_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[database["id"], order["id"]]
    )
    xml_base = f"/api/v1/resources/{order['id']}/order-configs"
    listed = client.get(xml_base, headers=admin_headers).json()
    xml_filename = listed["files"][0]["name"]
    detail = client.get(f"{xml_base}/{xml_filename}", headers=admin_headers).json()
    xml_with_contracts = detail["content"].replace(
        "</tcp>", '<read_symbol_csv value="1" />\n</tcp>'
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
    assert published_order["config"]["xml_checksum"] == updated.json()["checksum"]

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

    cpu_value = workflows.SIMULATED_VALUES["cpu_model"]
    monkeypatch.delitem(workflows.SIMULATED_VALUES, "cpu_model")
    assert client.post(f"/api/v1/runs/{created['id']}/start", headers=admin_headers).status_code == 200
    failed = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
    assert failed["status"] == "execution_failed"
    assert failed["steps"][0]["result_summary"] == {"snapshot_ids": [1], "sources": 1, "failed": 1}

    monkeypatch.setitem(workflows.SIMULATED_VALUES, "cpu_model", cpu_value)
    retried = client.post(f"/api/v1/runs/{created['id']}/retry", headers=admin_headers)
    assert retried.status_code == 200, retried.text
    completed = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
    assert completed["status"] == "completed"
    assert completed["steps"][0]["retry_count"] == 1
    with SessionLocal() as db:
        snapshots = db.query(workflows.ConfigurationCaptureSnapshot).order_by(
            workflows.ConfigurationCaptureSnapshot.id
        ).all()
        assert [item.attempt for item in snapshots] == [1, 2]
        assert [entry.status for entry in snapshots[0].items] == ["succeeded", "failed"]
        assert all(entry.status == "succeeded" for entry in snapshots[1].items)
