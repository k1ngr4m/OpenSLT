from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import WorkflowDocumentWrite
from app.workflow_node_configs import MarketStartupConfig, OrderPreparationConfig, ParserConfig, RemStartupConfig, ServerConfig, StatisticsConfig, WiringConfirmationConfig, parse_node_config


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
    rem_startup = parse_node_config("rem_startup", {})
    assert isinstance(rem_startup, RemStartupConfig)

    market_startup = parse_node_config("market_startup", {
        "scripts": [{"filename": "start.sh", "checksum": "a" * 64}],
    })
    assert isinstance(market_startup, MarketStartupConfig)
    assert market_startup.scripts[0].filename == "start.sh"

    with pytest.raises(ValidationError):
        parse_node_config("market_startup", {
            "scripts": [{"filename": "../start.sh", "checksum": "a" * 64}],
        })

    with pytest.raises(ValidationError):
        parse_node_config("market_startup", {
            "scripts": [
                {"filename": "start.sh", "checksum": "a" * 64},
                {"filename": "start.sh", "checksum": "a" * 64},
            ],
        })

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

    parser = parse_node_config("parser_parse", {
        "database_name": "fut_mm_trading_data",
        "config_xml_filename": "config-test.xml",
        "config_xml_checksum": "a" * 64,
        "instance_xml_filename": "instance-test.xml",
        "instance_xml_checksum": "b" * 64,
        "analysis_xml_filename": "soft_cffex_speed_analysis.xml",
        "analysis_xml_checksum": "c" * 64,
    })
    assert isinstance(parser, ParserConfig)
    assert parser.analysis_xml_filename == "soft_cffex_speed_analysis.xml"

    statistics = parse_node_config("data_statistics", {
        "parser_node_key": "parse",
        "script_filename": "statistics_cffex.py",
        "script_checksum": "d" * 64,
    })
    assert isinstance(statistics, StatisticsConfig)
    assert statistics.max_latency_ns == 999999999


def test_wiring_interface_names_are_trimmed_and_bounded() -> None:
    config = parse_node_config("wiring_confirmation", {
        "diagram": "resource",
        "client_interface_name": " client0 ",
        "market_interface_name": " market0 ",
        "auxiliary_interface_names": [" aux0 ", "aux1"],
    })
    assert isinstance(config, WiringConfirmationConfig)
    assert config.client_interface_name == "client0"
    assert config.market_interface_name == "market0"
    assert config.auxiliary_interface_names == ["aux0", "aux1"]

    with pytest.raises(ValidationError):
        parse_node_config("wiring_confirmation", {
            "client_interface_name": "x" * 33,
        })

    with pytest.raises(ValidationError):
        parse_node_config("wiring_confirmation", {
            "auxiliary_interface_names": ["3(mac2)", "4(mac3)", "5(mac4)"],
        })
