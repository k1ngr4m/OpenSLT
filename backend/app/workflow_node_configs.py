from __future__ import annotations

import typing

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Literal


ORDER_ACTIONS = (
    "new_order",
    "new_order_simple",
    "new_quote",
    "new_quote_simple",
    "new_arbi_order",
    "new_arbi_order_simple",
    "cxl_order",
    "stop_order",
)
OrderAction = Literal[
    "new_order",
    "new_order_simple",
    "new_quote",
    "new_quote_simple",
    "new_arbi_order",
    "new_arbi_order_simple",
    "cxl_order",
    "stop_order",
]


class WorkflowNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerTargetConfig(WorkflowNodeConfig):
    resource_type: Literal["rem", "market", "order"]
    fields: typing.List[
        Literal["ip", "nic_model", "machine_model", "os_version", "cpu_model"]
    ] = Field(default_factory=list)


class ServerConfig(WorkflowNodeConfig):
    targets: typing.List[ServerTargetConfig] = Field(default_factory=list)


class DatabaseConfig(WorkflowNodeConfig):
    database_name: str = ""
    keys: typing.List[str] = Field(default_factory=list)


class WiringConfirmationConfig(WorkflowNodeConfig):
    diagram: str = "placeholder"


class OrderPreparationConfig(WorkflowNodeConfig):
    xml_filename: str = ""
    xml_checksum: str = ""
    network_interface: str = ""
    read_symbol_csv: Literal[0, 1] = 0
    database_node_key: str = ""
    trading_database_name: str = ""
    contract_file_ids: typing.List[int] = Field(default_factory=list)
    order_action: OrderAction = "new_order"


class SlnicStartConfig(WorkflowNodeConfig):
    pass


class SlnicStopConfig(WorkflowNodeConfig):
    pass


class SlnicMergeConfig(WorkflowNodeConfig):
    pass


class ParserConfig(WorkflowNodeConfig):
    database_name: str = ""
    config_xml_filename: str = ""
    config_xml_checksum: str = ""
    instance_xml_filename: str = ""
    instance_xml_checksum: str = ""
    analysis_xml_filename: str = ""
    analysis_xml_checksum: str = ""


class StatisticsConfig(WorkflowNodeConfig):
    parser_node_key: str = ""
    script_filename: str = ""
    script_checksum: str = ""
    max_latency_ns: int = Field(default=999999999, ge=1)


NodeConfig = typing.Union[
    ServerConfig,
    DatabaseConfig,
    WiringConfirmationConfig,
    OrderPreparationConfig,
    SlnicStartConfig,
    SlnicStopConfig,
    SlnicMergeConfig,
    ParserConfig,
    StatisticsConfig,
]


NODE_CONFIG_MODELS: typing.Dict[str, typing.Type[WorkflowNodeConfig]] = {
    "server_config": ServerConfig,
    "database_config": DatabaseConfig,
    "wiring_confirmation": WiringConfirmationConfig,
    "order_preparation": OrderPreparationConfig,
    "slnic_start_capture": SlnicStartConfig,
    "slnic_stop_capture": SlnicStopConfig,
    "slnic_merge_capture": SlnicMergeConfig,
    "parser_parse": ParserConfig,
    "data_statistics": StatisticsConfig,
}


def parse_node_config(node_type: str, config: typing.Mapping[str, typing.Any]) -> NodeConfig:
    model = NODE_CONFIG_MODELS.get(node_type)
    if model is None:
        raise ValueError("unsupported workflow node type: %s" % node_type)
    return model.model_validate(config)
