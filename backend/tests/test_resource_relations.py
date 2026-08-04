from __future__ import annotations

from sqlalchemy import select, text

from app.core.database import SessionLocal
from app.models import (
    ContractDataFile,
    PlanResource,
    RunResource,
    ScenarioResource,
    ScenarioWorkflowNode,
    TestPlan as PlanModel,
    WorkflowNodeContractFile,
    WorkflowVersionResource,
)
from app.services.relation_consistency import find_relation_drifts, repair_relation_drifts
from app.services.resource_relations import sync_node_contract_files, sync_plan_resources
from conftest import create_plan_scenario, create_resource, publish_workflow


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


def test_relation_consistency_audit_and_repair(client, admin_headers) -> None:
    resource = create_resource(client, admin_headers, "REM-consistency")
    plan, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[resource["id"]],
    )
    with SessionLocal() as db:
        plan_model = db.get(PlanModel, plan["id"])
        sync_plan_resources(plan_model, [resource["id"]], db)
        node = ScenarioWorkflowNode(
            workflow_version_id=scenario["draft_workflow_version_id"],
            node_key="contract-node",
            position=1,
            node_type="order_preparation",
            name="发单准备",
            config={"contract_file_ids": []},
        )
        db.add(node)
        db.flush()
        contract_file = ContractDataFile(
            scenario_id=scenario["id"],
            workflow_node_id=node.id,
            order_resource_id=resource["id"],
            contract_type="futures",
            source_table="t_close_report",
            filename="contracts.csv",
            remote_path="/tmp/contracts.csv",
            archive_path="/tmp/contracts.csv",
            row_count=1,
            size=10,
            checksum="a" * 64,
            preview_rows=[],
            created_by=1,
        )
        db.add(contract_file)
        db.flush()
        sync_node_contract_files(node, [contract_file.id], db)
        db.commit()

        db.execute(
            text("UPDATE t_test_plans SET default_resource_ids = '[]' WHERE id = :id"),
            {"id": plan["id"]},
        )
        db.execute(
            text("UPDATE t_scenario_workflow_nodes SET config = '{}' WHERE id = :id"),
            {"id": node.id},
        )
        db.commit()
        db.expire_all()

        drifts = find_relation_drifts(db)
        assert {(item.relation, item.owner_id) for item in drifts} == {
            ("plan_resources", plan["id"]),
            ("workflow_node_contract_files", node.id),
        }
        assert repair_relation_drifts(db, "relations") == 2
        db.commit()
        assert find_relation_drifts(db) == []
        assert db.scalars(
            select(PlanResource.resource_id).where(PlanResource.plan_id == plan["id"])
        ).all() == [resource["id"]]
        assert db.scalars(
            select(WorkflowNodeContractFile.contract_file_id).where(
                WorkflowNodeContractFile.workflow_node_id == node.id
            )
        ).all() == [contract_file.id]
