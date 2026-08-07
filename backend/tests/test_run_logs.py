from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models import LogRecord
from conftest import create_plan_scenario, create_resource, publish_workflow


def create_run(client: TestClient, headers: dict[str, str]) -> dict:
    resource = create_resource(client, headers, "REM-log-cursor")
    plan, scenario = create_plan_scenario(client, headers, resource_ids=[resource["id"]])
    publish_workflow(
        client,
        headers,
        scenario,
        [resource["id"]],
        [
            {
                "node_key": "wiring",
                "node_type": "wiring_confirmation",
                "name": "确认接线",
                "config": {"diagram": "placeholder"},
            }
        ],
    )
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_run_logs_support_incremental_after_id_and_existing_filters(
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    run = create_run(client, admin_headers)
    with SessionLocal() as db:
        records = [
            LogRecord(
                log_type="run",
                level="INFO",
                event="cursor.first",
                message="first",
                trace_id=run["trace_id"],
                run_id=run["id"],
                source="worker",
                detail={},
            ),
            LogRecord(
                log_type="run",
                level="WARNING",
                event="cursor.second",
                message="second",
                trace_id=run["trace_id"],
                run_id=run["id"],
                source="worker",
                detail={},
            ),
            LogRecord(
                log_type="run",
                level="ERROR",
                event="cursor.third",
                message="third",
                trace_id=run["trace_id"],
                run_id=run["id"],
                source="api",
                detail={},
            ),
        ]
        db.add_all(records)
        db.flush()
        ids = [record.id for record in records]
        db.commit()

    initial = client.get(f"/api/v1/runs/{run['id']}/logs", headers=admin_headers)
    assert initial.status_code == 200
    assert [item["id"] for item in initial.json()] == ids

    incremental = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": ids[0]},
    )
    assert incremental.status_code == 200
    assert [item["id"] for item in incremental.json()] == ids[1:]

    filtered = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": ids[0], "source": "api", "level": "ERROR"},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [ids[2]]

    invalid = client.get(
        f"/api/v1/runs/{run['id']}/logs",
        headers=admin_headers,
        params={"after_id": -1},
    )
    assert invalid.status_code == 422
