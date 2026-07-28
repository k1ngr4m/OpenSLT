from __future__ import annotations

import copy
import typing

from fastapi.testclient import TestClient

from conftest import create_plan_scenario


def wiring_profile(client_ip: str = "180.1.1.101", market_ip: str = "10.1.51.101") -> dict:
    return {
        "client_switch_label": "180段交换机（客户端）",
        "market_switch_label": "51段交换机（市场端）",
        "client_interface": {"name": "enp101s0d1", "ip_address": client_ip},
        "market_interface": {"name": "enp23s0", "ip_address": market_ip},
    }


def resource_payload(
    name: str,
    resource_type: str,
    *,
    profile: typing.Optional[dict] = None,
) -> dict:
    return {
        "name": name,
        "resource_type": resource_type,
        "business_code": "fut_mm",
        "host": "10.1.51.8" if resource_type == "rem" else "10.1.51.210",
        "ssh_port": 22,
        "username": "tester",
        "auth_type": "password",
        "password": "secret",
        "remote_path": "/tmp/openslt",
        "capabilities": {},
        "wiring_profile": profile,
        "version_info": "test",
        "notes": "",
        "is_enabled": True,
    }


def create_resource(client: TestClient, headers: dict[str, str], payload: dict) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def save_wiring_workflow(
    client: TestClient,
    headers: dict[str, str],
    scenario: dict,
    resource_ids: list[int],
) -> None:
    document = client.get(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=headers).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": resource_ids,
            "nodes": [{
                "node_key": "wiring",
                "node_type": "wiring_confirmation",
                "name": "确认接线",
                "config": {"diagram": "resource"},
            }],
        },
    )
    assert saved.status_code == 200, saved.text


def test_rem_wiring_profile_round_trip_and_validation(client: TestClient, admin_headers: dict[str, str]) -> None:
    rem = create_resource(client, admin_headers, resource_payload("REM-01", "rem", profile=wiring_profile()))
    assert rem["wiring_profile"]["client_interface"] == {
        "name": "enp101s0d1",
        "ip_address": "180.1.1.101",
    }

    invalid = resource_payload("REM-invalid", "rem", profile=wiring_profile(client_ip="10.1.1.999"))
    response = client.post("/api/v1/resources", headers=admin_headers, json=invalid)
    assert response.status_code == 422

    slnic_payload = resource_payload("SLNIC-01", "slnic", profile=wiring_profile())
    slnic = create_resource(client, admin_headers, slnic_payload)
    assert slnic["wiring_profile"] is None


def test_resource_wiring_publish_validation(client: TestClient, admin_headers: dict[str, str]) -> None:
    rem = create_resource(client, admin_headers, resource_payload("REM-missing", "rem"))
    slnic = create_resource(client, admin_headers, resource_payload("SLNIC-01", "slnic"))
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"], slnic["id"]])
    save_wiring_workflow(client, admin_headers, scenario, [rem["id"], slnic["id"]])

    response = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert response.status_code == 422
    assert response.json()["code"] == "WORKFLOW_VALIDATION_FAILED"
    assert any(item["field"] == "wiring_profile" for item in response.json()["errors"])


def test_resource_wiring_requires_slnic(client: TestClient, admin_headers: dict[str, str]) -> None:
    rem = create_resource(client, admin_headers, resource_payload("REM-01", "rem", profile=wiring_profile()))
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    save_wiring_workflow(client, admin_headers, scenario, [rem["id"]])

    response = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert response.status_code == 422
    assert any("SLNIC" in item["message"] for item in response.json()["errors"])


def test_run_snapshots_replacement_wiring_profile(client: TestClient, admin_headers: dict[str, str]) -> None:
    default_rem = create_resource(client, admin_headers, resource_payload("REM-default", "rem", profile=wiring_profile()))
    replacement_payload = resource_payload(
        "REM-replacement",
        "rem",
        profile=wiring_profile(client_ip="180.1.1.188", market_ip="10.1.51.188"),
    )
    replacement_rem = create_resource(client, admin_headers, replacement_payload)
    slnic = create_resource(client, admin_headers, resource_payload("SLNIC-01", "slnic"))
    plan, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[default_rem["id"], slnic["id"]],
    )
    save_wiring_workflow(client, admin_headers, scenario, [default_rem["id"], slnic["id"]])
    published = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert published.status_code == 200, published.text

    created = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [replacement_rem["id"], slnic["id"]],
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()
    snapshot = run["steps"][0]["config_snapshot"]["wiring_snapshot"]
    assert snapshot["rem"]["id"] == replacement_rem["id"]
    assert snapshot["client_interface"]["ip_address"] == "180.1.1.188"
    assert snapshot["slnic_ports"] == [
        {"port": 0, "side": "client", "direction": "uplink", "label": "客户端上行"},
        {"port": 1, "side": "market", "direction": "uplink", "label": "市场上行"},
        {"port": 2, "side": "market", "direction": "downlink", "label": "市场下行"},
        {"port": 3, "side": "client", "direction": "downlink", "label": "客户端下行"},
    ]

    changed_payload = copy.deepcopy(replacement_payload)
    changed_payload["wiring_profile"]["client_interface"]["ip_address"] = "180.1.1.199"
    updated = client.put(f"/api/v1/resources/{replacement_rem['id']}", headers=admin_headers, json=changed_payload)
    assert updated.status_code == 200, updated.text

    reloaded = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    assert reloaded["steps"][0]["config_snapshot"]["wiring_snapshot"]["client_interface"]["ip_address"] == "180.1.1.188"
