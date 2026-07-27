from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import RunResource, ScenarioResource, WorkflowVersionResource
from backend.tests.conftest import create_plan_scenario, create_resource, publish_workflow


def test_resource_relations_are_dual_written_for_scenario_workflow_and_run(
    client,
    admin_headers,
) -> None:
    resource = create_resource(client, admin_headers, "REM-normalized")
    plan, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[resource["id"]],
    )
    publish_workflow(
        client,
        admin_headers,
        scenario,
        [resource["id"]],
        [
            {
                "node_key": "server-capture",
                "node_type": "server_config",
                "name": "采集服务器配置",
                "config": {
                    "targets": [{"resource_type": "rem", "fields": ["ip"]}],
                },
            }
        ],
    )
    response = client.post(
        "/api/v1/runs",
        headers=admin_headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [resource["id"]],
            "timeout_minutes": 30,
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()

    with SessionLocal() as db:
        assert db.scalars(
            select(ScenarioResource.resource_id)
            .where(ScenarioResource.scenario_id == scenario["id"])
            .order_by(ScenarioResource.position)
        ).all() == [resource["id"]]
        assert db.scalars(
            select(WorkflowVersionResource.resource_id)
            .where(WorkflowVersionResource.workflow_version_id == run["workflow_version_id"])
            .order_by(WorkflowVersionResource.position)
        ).all() == [resource["id"]]
        assert db.scalars(
            select(RunResource.resource_id)
            .where(RunResource.run_id == run["id"])
            .order_by(RunResource.position)
        ).all() == [resource["id"]]

    deleted = client.delete(f"/api/v1/resources/{resource['id']}", headers=admin_headers)
    assert deleted.status_code == 204
    assert resource["id"] not in {
        item["id"] for item in client.get("/api/v1/resources", headers=admin_headers).json()
    }
