from __future__ import annotations

import typing

from fastapi.testclient import TestClient

from conftest import create_plan_scenario


def plan_payload(*, directory_id: typing.Optional[int] = None, name: str = "目录方案") -> dict:
    payload = {
        "name": name,
        "business_code": "fut_mm",
        "description": "",
        "default_resource_ids": [],
        "config_version": "1.0",
        "is_enabled": True,
    }
    if directory_id is not None:
        payload["directory_id"] = directory_id
    return payload


def test_default_directory_and_duplicate_names(
    client: TestClient, admin_headers: typing.Dict[str, str]
) -> None:
    listed = client.get("/api/v1/plan-directories", headers=admin_headers)
    assert listed.status_code == 200
    assert [(item["name"], item["is_default"]) for item in listed.json()] == [
        ("默认目录", True)
    ]

    first = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "  回归测试  "}
    )
    second = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "回归测试"}
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["name"] == second.json()["name"] == "回归测试"
    assert first.json()["id"] != second.json()["id"]

    blank = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "   "}
    )
    assert blank.status_code == 422


def test_default_directory_is_protected_and_empty_directory_can_be_deleted(
    client: TestClient, admin_headers: typing.Dict[str, str]
) -> None:
    default_directory = client.get(
        "/api/v1/plan-directories", headers=admin_headers
    ).json()[0]

    renamed = client.put(
        f"/api/v1/plan-directories/{default_directory['id']}",
        headers=admin_headers,
        json={"name": "其他名称"},
    )
    assert renamed.status_code == 409
    assert renamed.json()["code"] == "DEFAULT_DIRECTORY_PROTECTED"

    deleted = client.delete(
        f"/api/v1/plan-directories/{default_directory['id']}", headers=admin_headers
    )
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "DEFAULT_DIRECTORY_PROTECTED"

    temporary = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "临时目录"}
    ).json()
    assert (
        client.delete(
            f"/api/v1/plan-directories/{temporary['id']}", headers=admin_headers
        ).status_code
        == 204
    )


def test_plans_default_move_copy_and_nonempty_directory_protection(
    client: TestClient, admin_headers: typing.Dict[str, str]
) -> None:
    default_directory = client.get(
        "/api/v1/plan-directories", headers=admin_headers
    ).json()[0]
    source = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "源目录"}
    ).json()
    destination = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "目标目录"}
    ).json()

    legacy_plan = client.post("/api/v1/plans", headers=admin_headers, json=plan_payload())
    assert legacy_plan.status_code == 201
    assert legacy_plan.json()["directory_id"] == default_directory["id"]

    plan = client.post(
        "/api/v1/plans",
        headers=admin_headers,
        json=plan_payload(directory_id=source["id"]),
    )
    assert plan.status_code == 201
    plan = plan.json()

    blocked = client.delete(
        f"/api/v1/plan-directories/{source['id']}", headers=admin_headers
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "DIRECTORY_NOT_EMPTY"

    moved_payload = plan_payload(directory_id=destination["id"], name=plan["name"])
    moved = client.put(
        f"/api/v1/plans/{plan['id']}", headers=admin_headers, json=moved_payload
    )
    assert moved.status_code == 200
    assert moved.json()["directory_id"] == destination["id"]

    legacy_update = client.put(
        f"/api/v1/plans/{plan['id']}",
        headers=admin_headers,
        json=plan_payload(name="旧客户端更新"),
    )
    assert legacy_update.status_code == 200
    assert legacy_update.json()["directory_id"] == destination["id"]

    copied = client.post(f"/api/v1/plans/{plan['id']}/copy", headers=admin_headers)
    assert copied.status_code == 201
    assert copied.json()["directory_id"] == destination["id"]
    assert (
        client.delete(
            f"/api/v1/plan-directories/{source['id']}", headers=admin_headers
        ).status_code
        == 204
    )

    filtered = client.get(
        "/api/v1/plans",
        headers=admin_headers,
        params={"directory_id": destination["id"]},
    )
    assert {item["id"] for item in filtered.json()} == {plan["id"], copied.json()["id"]}

    invalid = client.post(
        "/api/v1/plans",
        headers=admin_headers,
        json=plan_payload(directory_id=999_999),
    )
    assert invalid.status_code == 404


def test_directory_permissions(
    client: TestClient, admin_headers: typing.Dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "directory-viewer",
            "display_name": "目录访客",
            "password": "viewer-password",
            "role": "visitor",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "directory-viewer", "password": "viewer-password"},
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.get("/api/v1/plan-directories", headers=viewer_headers).status_code == 200
    assert (
        client.post(
            "/api/v1/plan-directories", headers=viewer_headers, json={"name": "无权限"}
        ).status_code
        == 403
    )


def test_scenario_cannot_be_reassigned_across_directories(
    client: TestClient, admin_headers: typing.Dict[str, str]
) -> None:
    _, scenario = create_plan_scenario(client, admin_headers)
    other_directory = client.post(
        "/api/v1/plan-directories", headers=admin_headers, json={"name": "其他目录"}
    ).json()
    other_plan = client.post(
        "/api/v1/plans",
        headers=admin_headers,
        json=plan_payload(directory_id=other_directory["id"], name="其他方案"),
    ).json()

    response = client.put(
        f"/api/v1/scenarios/{scenario['id']}",
        headers=admin_headers,
        json={
            "plan_id": other_plan["id"],
            "name": scenario["name"],
            "scenario_type": "order",
            "config_version": "1.0",
            "expected_artifacts": [],
            "default_resource_ids": None,
            "required_resource_types": scenario["required_resource_types"],
            "is_enabled": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SCENARIO_CROSS_DIRECTORY_FORBIDDEN"
