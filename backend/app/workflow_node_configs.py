from __future__ import annotations

import typing

from pydantic import BaseModel, ConfigDict, Field, field_validator
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
REM_STARTUP_DEFAULT_COMMANDS = (
    "./stop_rem.sh",
    "./makeneat.sh",
    "./start_rem_all.sh",
)
REM_STARTUP_MAX_COMMANDS = 100
REM_STARTUP_MAX_COMMAND_LENGTH = 4096
REM_STARTUP_MAX_TOTAL_BYTES = 32 * 1024
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
    client_interface_name: typing.Optional[str] = Field(default=None, max_length=32)
    market_interface_name: typing.Optional[str] = Field(default=None, max_length=32)
    auxiliary_interface_names: typing.Optional[typing.List[str]] = Field(
        default=None, max_length=2
    )

    @field_validator("client_interface_name", "market_interface_name", mode="before")
    @classmethod
    def trim_interface_name(cls, value: typing.Any) -> typing.Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("auxiliary_interface_names", mode="before")
    @classmethod
    def trim_auxiliary_interface_names(cls, value: typing.Any) -> typing.Any:
        if not isinstance(value, list):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]

    @field_validator("auxiliary_interface_names")
    @classmethod
    def validate_auxiliary_interface_name_lengths(
        cls, value: typing.Optional[typing.List[str]]
    ) -> typing.Optional[typing.List[str]]:
        if value is not None and any(len(item) > 32 for item in value):
            raise ValueError("接线接口名称不能超过 32 个字符")
        return value


class RemStartupConfig(WorkflowNodeConfig):
    commands: typing.List[str] = Field(
        default_factory=lambda: list(REM_STARTUP_DEFAULT_COMMANDS),
        max_length=REM_STARTUP_MAX_COMMANDS,
    )

    @field_validator("commands", mode="before")
    @classmethod
    def normalize_commands(cls, value: typing.Any) -> typing.Any:
        if not isinstance(value, list):
            return value
        commands: typing.List[typing.Any] = []
        for item in value:
            if not isinstance(item, str):
                commands.append(item)
                continue
            commands.extend(line.strip() for line in item.splitlines() if line.strip())
        return commands

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: typing.List[str]) -> typing.List[str]:
        if any(len(command) > REM_STARTUP_MAX_COMMAND_LENGTH for command in value):
            raise ValueError("单条 REM 启动命令不能超过 4096 个字符")
        if sum(len(command.encode("utf-8")) for command in value) > REM_STARTUP_MAX_TOTAL_BYTES:
            raise ValueError("REM 启动命令总长度不能超过 32 KiB")
        return value


class MarketScriptSelection(WorkflowNodeConfig):
    filename: str = Field(pattern=r"^[A-Za-z0-9._-]+\.sh$")
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class MarketStartupConfig(WorkflowNodeConfig):
    scripts: typing.List[MarketScriptSelection] = Field(default_factory=list)

    @field_validator("scripts")
    @classmethod
    def reject_duplicate_scripts(
        cls, value: typing.List[MarketScriptSelection]
    ) -> typing.List[MarketScriptSelection]:
        filenames = [item.filename for item in value]
        if len(filenames) != len(set(filenames)):
            raise ValueError("模拟市场启动脚本不能重复")
        return value


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
    # Kept for published workflow/run snapshot compatibility. Statistics inputs
    # are selected from the parser resource at run time and no longer use it.
    parser_node_key: str = ""
    script_filename: str = ""
    script_checksum: str = ""
    max_latency_ns: int = Field(default=999999999, ge=1)


class ReportGenerationConfig(WorkflowNodeConfig):
    pass


NodeConfig = typing.Union[
    ServerConfig,
    DatabaseConfig,
    WiringConfirmationConfig,
    RemStartupConfig,
    MarketStartupConfig,
    OrderPreparationConfig,
    SlnicStartConfig,
    SlnicStopConfig,
    SlnicMergeConfig,
    ParserConfig,
    StatisticsConfig,
    ReportGenerationConfig,
]


NODE_CONFIG_MODELS: typing.Dict[str, typing.Type[WorkflowNodeConfig]] = {
    "server_config": ServerConfig,
    "database_config": DatabaseConfig,
    "wiring_confirmation": WiringConfirmationConfig,
    "rem_startup": RemStartupConfig,
    "market_startup": MarketStartupConfig,
    "order_preparation": OrderPreparationConfig,
    "slnic_start_capture": SlnicStartConfig,
    "slnic_stop_capture": SlnicStopConfig,
    "slnic_merge_capture": SlnicMergeConfig,
    "parser_parse": ParserConfig,
    "data_statistics": StatisticsConfig,
    "report_generation": ReportGenerationConfig,
}


def parse_node_config(node_type: str, config: typing.Mapping[str, typing.Any]) -> NodeConfig:
    model = NODE_CONFIG_MODELS.get(node_type)
    if model is None:
        raise ValueError("unsupported workflow node type: %s" % node_type)
    return model.model_validate(config)
