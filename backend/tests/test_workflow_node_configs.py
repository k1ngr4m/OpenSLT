from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import WorkflowDocumentWrite
from app.workflow_node_configs import OrderPreparationConfig, ServerConfig, parse_node_config


def test_workflow_document_discriminates_node_config_by_node_type() -> None:
    document = WorkflowDocumentWrite.model_validate({
        "expected_revision": 1,
        "resource_ids": [1],
        "nodes": [{
            "node_key": "server",
            "node_type": "server_config",
            "name": "Server capture",
            "config": {
                "targets": [{"resource_type": "rem", "fields": ["ip", "cpu_model"]}],
            },
        }],
    })
    assert isinstance(document.nodes[0].config, ServerConfig)
    assert document.nodes[0].config.targets[0].resource_type == "rem"

    with pytest.raises(ValidationError):
        WorkflowDocumentWrite.model_validate({
            "expected_revision": 1,
            "resource_ids": [1],
            "nodes": [{
                "node_key": "server",
                "node_type": "server_config",
                "name": "Wrong config",
                "config": {"xml_filename": "order.xml"},
            }],
        })


def test_runtime_config_parser_uses_the_same_contract() -> None:
    config = parse_node_config("order_preparation", {
        "xml_filename": "order.xml",
        "network_interface": "p4p1",
        "read_symbol_csv": 1,
        "contract_file_ids": [3, 5],
    })
    assert isinstance(config, OrderPreparationConfig)
    assert config.contract_file_ids == [3, 5]

    with pytest.raises(ValidationError):
        parse_node_config("order_preparation", {"read_symbol_csv": 2})
