from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import WorkflowNodeWrite
from app.services.workflow_handlers.registry import registry
from conftest import create_plan_scenario


def test_report_generation_node_schema_and_handler_registration():
    assert "report_generation" in registry.node_types
    adapter = TypeAdapter(WorkflowNodeWrite)
    parsed = adapter.validate_python({
        "node_key": "report", "node_type": "report_generation",
        "name": "生成报告", "config": {},
    })
    assert parsed.config.model_dump() == {}
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "node_key": "report", "node_type": "report_generation",
            "name": "生成报告", "config": {"format": "pdf"},
        })


def test_report_generation_structure_constraints(client, admin_headers):
    parser_response = client.post("/api/v1/resources", headers=admin_headers, json={
        "name": "Parser-report", "resource_type": "parser", "business_code": "fut_mm",
        "host": "127.0.0.1", "ssh_port": 22, "username": "tester",
        "auth_type": "password", "password": "secret", "remote_path": "/tmp/parser",
        "capabilities": {"parser_tool": "soft_cffex_speed_analysis_v2"},
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert parser_response.status_code == 201, parser_response.text
    parser = parser_response.json()
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["parser"], resource_ids=[parser["id"]]
    )
    endpoint = f"/api/v1/scenarios/{scenario['id']}/workflow"
    revision = client.get(endpoint, headers=admin_headers).json()["draft"]["revision"]
    statistics = {
        "node_key": "statistics", "node_type": "data_statistics", "name": "统计",
        "config": {
            "parser_node_key": "", "script_filename": "statistics.py",
            "script_checksum": "a" * 64, "max_latency_ns": 999999999,
        },
    }
    report = {
        "node_key": "report", "node_type": "report_generation",
        "name": "生成报告", "config": {},
    }

    def save(nodes):
        nonlocal revision
        response = client.put(endpoint, headers=admin_headers, json={
            "expected_revision": revision,
            "resource_ids": [parser["id"]],
            "nodes": nodes,
        })
        assert response.status_code == 200, response.text
        revision = response.json()["draft"]["revision"]
        return {item["message"] for item in response.json()["validation_errors"]}

    assert "报告生成节点前至少需要一个数据统计节点" in save([report])
    assert "报告生成节点必须位于工作流末尾" in save([
        statistics, report, {**statistics, "node_key": "statistics-2"},
    ])
    assert "每个工作流最多只能有一个报告生成节点" in save([
        statistics, report, {**report, "node_key": "report-2"},
    ])
    report_messages = {message for message in save([statistics, report]) if "报告生成节点" in message}
    assert report_messages == set()
