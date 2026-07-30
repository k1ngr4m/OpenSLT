from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from conftest import create_plan_scenario


REM_CONFIG = {
    "trade_ip": "180.1.1.101",
    "trade_tcp_port": 10001,
    "trade_udp_port": 10002,
    "query_ip": "180.1.1.102",
    "query_port": 10003,
}


def resource_payload(name: str, resource_type: str, **overrides) -> dict:
    hosts = {"rem": "10.1.51.8", "market": "10.1.51.101", "slnic": "10.1.51.210"}
    payload = {
        "name": name,
        "resource_type": resource_type,
        "business_code": "fut_mm",
        "host": hosts[resource_type],
        "ssh_port": 22,
        "username": "tester",
        "auth_type": "password",
        "password": "secret",
        "remote_path": "/tmp/openslt",
        "capabilities": {},
        "version_info": "test",
        "notes": "",
        "is_enabled": True,
    }
    if resource_type == "rem":
        payload.update(REM_CONFIG)
    payload.update(overrides)
    return payload


def create_resource(client: TestClient, headers: dict[str, str], payload: dict) -> dict:
    response = client.post("/api/v1/resources", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def save_wiring_workflow(
    client: TestClient,
    headers: dict[str, str],
    scenario: dict,
    resource_ids: list[int],
    config: dict | None = None,
) -> dict:
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
                "config": config or {"diagram": "resource"},
            }],
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def create_business_scenario(
    client: TestClient,
    headers: dict[str, str],
    business_code: str,
    resource_ids: list[int],
) -> tuple[dict, dict]:
    plan_response = client.post(
        "/api/v1/plans",
        headers=headers,
        json={
            "name": f"{business_code}-plan",
            "business_code": business_code,
            "description": "wiring test",
            "default_resource_ids": resource_ids,
            "config_version": "1.0",
            "is_enabled": True,
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    scenario_response = client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "name": f"{business_code}-scenario",
            "scenario_type": "order",
            "config_version": "1.0",
            "expected_artifacts": ["pcapng"],
            "default_resource_ids": resource_ids,
            "required_resource_types": ["rem", "market", "slnic"],
            "is_enabled": True,
        },
    )
    assert scenario_response.status_code == 201, scenario_response.text
    return plan, scenario_response.json()


def test_rem_more_config_round_trip_and_validation(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    rem = create_resource(client, admin_headers, resource_payload("REM-01", "rem"))
    assert {key: rem[key] for key in REM_CONFIG} == REM_CONFIG

    missing = resource_payload("REM-missing", "rem")
    del missing["query_port"]
    response = client.post("/api/v1/resources", headers=admin_headers, json=missing)
    assert response.status_code == 422

    invalid_ip = resource_payload("REM-invalid-ip", "rem", trade_ip="10.1.1.999")
    response = client.post("/api/v1/resources", headers=admin_headers, json=invalid_ip)
    assert response.status_code == 422

    invalid_port = resource_payload("REM-invalid-port", "rem", trade_tcp_port=65536)
    response = client.post("/api/v1/resources", headers=admin_headers, json=invalid_port)
    assert response.status_code == 422

    slnic = create_resource(
        client,
        admin_headers,
        resource_payload("SLNIC-01", "slnic", **REM_CONFIG),
    )
    assert all(slnic[key] is None for key in REM_CONFIG)

    market = create_resource(
        client,
        admin_headers,
        resource_payload("Market-empty-ips", "market", trade_ip="", query_ip=""),
    )
    assert market["trade_ip"] is None
    assert market["query_ip"] is None


def test_resource_wiring_requires_market_and_slnic(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    rem = create_resource(client, admin_headers, resource_payload("REM-01", "rem"))
    _, scenario = create_plan_scenario(client, admin_headers, resource_ids=[rem["id"]])
    save_wiring_workflow(client, admin_headers, scenario, [rem["id"]])

    response = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert response.status_code == 422
    messages = [item["message"] for item in response.json()["errors"]]
    assert any("模拟市场" in message for message in messages)
    assert any("SLNIC" in message for message in messages)


def test_run_snapshots_selected_resource_ips(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    default_rem = create_resource(client, admin_headers, resource_payload("REM-default", "rem"))
    replacement_payload = resource_payload("REM-replacement", "rem", trade_ip="180.1.1.188")
    replacement_rem = create_resource(client, admin_headers, replacement_payload)
    market = create_resource(client, admin_headers, resource_payload("Market-01", "market"))
    slnic = create_resource(client, admin_headers, resource_payload("SLNIC-01", "slnic"))
    default_ids = [default_rem["id"], market["id"], slnic["id"]]
    plan, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=default_ids,
    )
    save_wiring_workflow(client, admin_headers, scenario, default_ids)
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers
    )
    assert published.status_code == 200, published.text

    created = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [replacement_rem["id"], market["id"], slnic["id"]],
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()
    snapshot = run["steps"][0]["config_snapshot"]["wiring_snapshot"]
    assert snapshot["schema_version"] == 2
    assert snapshot["rem"]["id"] == replacement_rem["id"]
    assert snapshot["client_interface"] == {
        "name": "enp101s0d1",
        "ip_address": "180.1.1.188",
    }
    assert snapshot["market"] == {
        "id": market["id"],
        "name": "Market-01",
        "host": "10.1.51.101",
    }
    assert snapshot["market_interface"]["ip_address"] == "10.1.51.101"
    assert snapshot["slnic"]["host"] == "10.1.51.210"
    assert snapshot["slnic_ports"] == [
        {"port": 0, "side": "client", "direction": "uplink", "label": "客户端上行"},
        {"port": 1, "side": "market", "direction": "uplink", "label": "市场上行"},
        {"port": 2, "side": "market", "direction": "downlink", "label": "市场下行"},
        {"port": 3, "side": "client", "direction": "downlink", "label": "客户端下行"},
    ]

    changed_payload = copy.deepcopy(replacement_payload)
    changed_payload["trade_ip"] = "180.1.1.199"
    updated = client.put(
        f"/api/v1/resources/{replacement_rem['id']}",
        headers=admin_headers,
        json=changed_payload,
    )
    assert updated.status_code == 200, updated.text

    reloaded = client.get(f"/api/v1/runs/{run['id']}", headers=admin_headers).json()
    frozen = reloaded["steps"][0]["config_snapshot"]["wiring_snapshot"]
    assert frozen["client_interface"]["ip_address"] == "180.1.1.188"


def test_integrated_wiring_names_are_saved_validated_and_frozen(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    business_code = "rem_two"
    rem = create_resource(
        client,
        admin_headers,
        resource_payload("REM-integrated", "rem", business_code=business_code),
    )
    market = create_resource(
        client,
        admin_headers,
        resource_payload("Market-integrated", "market", business_code=business_code),
    )
    slnic = create_resource(
        client,
        admin_headers,
        resource_payload("SLNIC-integrated", "slnic", business_code=business_code),
    )
    resource_ids = [rem["id"], market["id"], slnic["id"]]
    plan, scenario = create_business_scenario(
        client, admin_headers, business_code, resource_ids
    )
    names = {
        "diagram": "resource",
        "client_interface_name": "client-custom",
        "market_interface_name": "market-custom",
        "auxiliary_interface_names": ["aux-custom-1", "aux-custom-2"],
    }
    saved = save_wiring_workflow(
        client, admin_headers, scenario, resource_ids, names
    )
    assert saved["draft"]["nodes"][0]["config"] == names

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
            "resource_ids": resource_ids,
        },
    )
    assert created.status_code == 201, created.text
    snapshot = created.json()["steps"][0]["config_snapshot"]["wiring_snapshot"]
    assert snapshot["client_interface"]["name"] == "client-custom"
    assert snapshot["market_interface"]["name"] == "market-custom"
    assert snapshot["auxiliary_interfaces"] == ["aux-custom-1", "aux-custom-2"]


def test_integrated_wiring_requires_all_four_interface_names(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    business_code = "rem_two_mm"
    resources = [
        create_resource(
            client,
            admin_headers,
            resource_payload(f"{kind}-blank", kind, business_code=business_code),
        )
        for kind in ("rem", "market", "slnic")
    ]
    resource_ids = [resource["id"] for resource in resources]
    _, scenario = create_business_scenario(
        client, admin_headers, business_code, resource_ids
    )
    save_wiring_workflow(
        client,
        admin_headers,
        scenario,
        resource_ids,
        {
            "diagram": "resource",
            "client_interface_name": "1(mac0)",
            "market_interface_name": "2(mac1)",
            "auxiliary_interface_names": ["", "4(mac3)"],
        },
    )
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish",
        headers=admin_headers,
    )
    assert published.status_code == 422
    assert any(
        "接口名称不能为空" in item["message"]
        for item in published.json()["errors"]
    )
