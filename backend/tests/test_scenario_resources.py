from __future__ import annotations

import typing
from fastapi.testclient import TestClient

from conftest import create_plan_scenario, create_resource


def create_plan(client: TestClient, headers: typing.Dict[str, str], business_code: str = "fut_mm") -> dict:
    response = client.post(
        "/api/v1/plans",
        headers=headers,
        json={
            "name": "资源绑定方案",
            "business_code": business_code,
            "description": "",
            "default_resource_ids": [],
            "config_version": "1.0",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def resource_payload(name: str, resource_type: str, *, business_code: str = "fut_mm", is_enabled: bool = True) -> dict:
    return {
        "name": name,
        "resource_type": resource_type,
        "business_code": business_code,
        "host": "127.0.0.1",
        "ssh_port": 22,
        "username": "tester",
        "auth_type": "password",
        "password": "secret",
        "remote_path": "/tmp/openslt",
        "capabilities": {},
        "version_info": "test",
        "notes": "",
        "is_enabled": is_enabled,
    }


def scenario_payload(plan_id: int, resource_ids: typing.List[int]) -> dict:
    return {
        "plan_id": plan_id,
        "name": "资源场景",
        "scenario_type": "order",
        "config_version": "1.0",
        "expected_artifacts": [],
        "default_resource_ids": resource_ids,
        "required_resource_types": ["ignored"],
        "is_enabled": True,
    }


def test_scenario_resources_are_derived_and_copied(client: TestClient, admin_headers: typing.Dict[str, str]):
    rem = create_resource(client, admin_headers, "REM-01")
    market = create_resource(client, admin_headers, "Market-01", resource_type="market")
    plan = create_plan(client, admin_headers)

    response = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [rem["id"], market["id"]]),
    )
    assert response.status_code == 201, response.text
    scenario = response.json()
    assert scenario["default_resource_ids"] == [rem["id"], market["id"]]
    assert scenario["required_resource_types"] == ["rem", "market"]
    assert not ({"parameters", "actions", "statistics_rules"} & scenario.keys())

    order = create_resource(client, admin_headers, "Order-01", resource_type="order")
    updated = client.put(
        f"/api/v1/scenarios/{scenario['id']}",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [market["id"], order["id"]]),
    )
    assert updated.status_code == 200, updated.text
    scenario = updated.json()
    assert scenario["default_resource_ids"] == [market["id"], order["id"]]
    assert scenario["required_resource_types"] == ["market", "order"]

    copied = client.post(f"/api/v1/scenarios/{scenario['id']}/copy", headers=admin_headers)
    assert copied.status_code == 201
    assert copied.json()["default_resource_ids"] == scenario["default_resource_ids"]

    copied_plan = client.post(f"/api/v1/plans/{plan['id']}/copy", headers=admin_headers)
    assert copied_plan.status_code == 201
    copied_scenarios = client.get(
        "/api/v1/scenarios",
        headers=admin_headers,
        params={"plan_id": copied_plan.json()["id"]},
    ).json()
    assert len(copied_scenarios) == 2
    assert all(item["default_resource_ids"] == scenario["default_resource_ids"] for item in copied_scenarios)


def test_scenario_resource_validation(client: TestClient, admin_headers: typing.Dict[str, str]):
    rem_one = create_resource(client, admin_headers, "REM-01")
    rem_two = create_resource(client, admin_headers, "REM-02")
    plan = create_plan(client, admin_headers)

    empty = client.post("/api/v1/scenarios", headers=admin_headers, json=scenario_payload(plan["id"], []))
    assert empty.status_code == 400
    assert empty.json()["code"] == "SCENARIO_RESOURCES_REQUIRED"

    duplicate_type = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [rem_one["id"], rem_two["id"]]),
    )
    assert duplicate_type.status_code == 400
    assert duplicate_type.json()["code"] == "DUPLICATE_RESOURCE_TYPES"

    cross_business_response = client.post(
        "/api/v1/resources",
        headers=admin_headers,
        json=resource_payload("REM-other", "rem", business_code="rem_two"),
    )
    cross_business = cross_business_response.json()
    mismatch = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [cross_business["id"]]),
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["code"] == "BUSINESS_MISMATCH"

    disabled_response = client.post(
        "/api/v1/resources",
        headers=admin_headers,
        json=resource_payload("REM-disabled", "rem", is_enabled=False),
    )
    disabled = disabled_response.json()
    invalid = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [disabled["id"]]),
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_RESOURCES"

    missing = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [999_999]),
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "INVALID_RESOURCES"

    deleted = create_resource(client, admin_headers, "REM-deleted")
    assert client.delete(f"/api/v1/resources/{deleted['id']}", headers=admin_headers).status_code == 204
    deleted_response = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [deleted["id"]]),
    )
    assert deleted_response.status_code == 400
    assert deleted_response.json()["code"] == "INVALID_RESOURCES"


def test_removed_scenario_json_fields_are_rejected(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "REM-01")
    plan = create_plan(client, admin_headers)
    payload = scenario_payload(plan["id"], [resource["id"]])
    payload["parameters"] = {}
    response = client.post("/api/v1/scenarios", headers=admin_headers, json=payload)
    assert response.status_code == 422


def test_legacy_type_only_scenario_is_still_supported(client: TestClient, admin_headers: typing.Dict[str, str]):
    _, scenario = create_plan_scenario(client, admin_headers, required_types=["rem"])
    assert scenario["default_resource_ids"] == []
    assert scenario["required_resource_types"] == ["rem"]


def test_run_resources_must_match_scenario_types(client: TestClient, admin_headers: typing.Dict[str, str]):
    rem_default = create_resource(client, admin_headers, "REM-default")
    rem_replacement = create_resource(client, admin_headers, "REM-replacement")
    market = create_resource(client, admin_headers, "Market-01", resource_type="market")
    order = create_resource(client, admin_headers, "Order-01", resource_type="order")
    plan = create_plan(client, admin_headers)
    scenario_response = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [rem_default["id"], market["id"]]),
    )
    scenario = scenario_response.json()

    valid = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem_replacement["id"], market["id"]], "timeout_minutes": 30},
    )
    assert valid.status_code == 201, valid.text

    missing = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem_default["id"]], "timeout_minutes": 30},
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "RESOURCE_SET_MISMATCH"

    duplicate = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem_default["id"], rem_replacement["id"], market["id"]], "timeout_minutes": 30},
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["code"] == "DUPLICATE_RESOURCE_TYPES"

    extra = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [rem_default["id"], market["id"], order["id"]], "timeout_minutes": 30},
    )
    assert extra.status_code == 400
    assert extra.json()["code"] == "RESOURCE_SET_MISMATCH"


def test_plan_and_scenario_can_be_deleted_without_run_history(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "REM-01")
    plan = create_plan(client, admin_headers)
    scenario_response = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [resource["id"]]),
    )
    scenario = scenario_response.json()

    deleted_scenario = client.delete(f"/api/v1/scenarios/{scenario['id']}", headers=admin_headers)
    assert deleted_scenario.status_code == 204
    remaining_scenario_ids = {item["id"] for item in client.get("/api/v1/scenarios", headers=admin_headers).json()}
    assert scenario["id"] not in remaining_scenario_ids

    deleted_plan = client.delete(f"/api/v1/plans/{plan['id']}", headers=admin_headers)
    assert deleted_plan.status_code == 204
    remaining_plan_ids = {item["id"] for item in client.get("/api/v1/plans", headers=admin_headers).json()}
    assert plan["id"] not in remaining_plan_ids


def test_plan_and_scenario_with_run_history_cannot_be_deleted(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_resource(client, admin_headers, "REM-01")
    plan = create_plan(client, admin_headers)
    scenario_response = client.post(
        "/api/v1/scenarios",
        headers=admin_headers,
        json=scenario_payload(plan["id"], [resource["id"]]),
    )
    scenario = scenario_response.json()
    run = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={"plan_id": plan["id"], "scenario_id": scenario["id"], "resource_ids": [resource["id"]], "timeout_minutes": 30},
    )
    assert run.status_code == 201

    scenario_delete = client.delete(f"/api/v1/scenarios/{scenario['id']}", headers=admin_headers)
    assert scenario_delete.status_code == 409
    assert scenario_delete.json()["code"] == "SCENARIO_REFERENCED"

    plan_delete = client.delete(f"/api/v1/plans/{plan['id']}", headers=admin_headers)
    assert plan_delete.status_code == 409
    assert plan_delete.json()["code"] == "PLAN_REFERENCED"
