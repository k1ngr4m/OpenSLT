from __future__ import annotations

import typing
import re
from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import Annotated

from app.services.run_state import RunStatus, StepStatus
from app.workflow_node_configs import (
    ORDER_ACTIONS,
    PARSER_ACTIONS,
    DatabaseConfig,
    MarketStartupConfig,
    OrderAction,
    OrderPreparationConfig,
    ParserConfig,
    RemStartupConfig,
    ReportGenerationConfig,
    StatisticsConfig,
    ServerConfig,
    SlnicMergeConfig,
    SlnicStartConfig,
    SlnicStopConfig,
    WiringConfirmationConfig,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(default="", max_length=128)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "tester", "visitor"] = "visitor"


class UserUpdate(BaseModel):
    display_name: typing.Union[str, None] = None
    role: typing.Union[Literal['admin', 'tester', 'visitor'], None] = None
    is_active: typing.Union[bool, None] = None
    password: typing.Union[str, None] = Field(default=None, min_length=8, max_length=128)


class UserOut(ORMModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    last_login_at: typing.Union[datetime, None]
    created_at: datetime


PARSER_TOOLS = {
    "soft_cffex_speed_analysis",
    "soft_cffex_speed_analysis_v2",
    "soft_shfe_speed_analysis_v2",
    "soft_czce_speed_analysis",
    "soft_dce_speed_analysis_v7",
    "soft_gfex_speed_analysis",
    "hwcffex_1414_2.0",
    "hwshfe_1414_2.0",
    "mg11",
}


class ResourceWrite(BaseModel):
    name: str
    resource_type: Literal["rem", "market", "order", "slnic", "capture", "coco", "parser", "database"]
    business_code: Literal["fut_mm", "rem_two", "rem_two_mm"]
    host: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    auth_type: Literal["password", "private_key"] = "password"
    password: typing.Union[str, None] = None
    private_key: typing.Union[str, None] = None
    database_engine: typing.Union[Literal['mysql'], None] = None
    database_connection_mode: typing.Union[Literal['direct', 'ssh_tunnel'], None] = None
    database_host: typing.Union[str, None] = None
    database_port: typing.Union[int, None] = Field(default=None, ge=1, le=65535)
    database_names: typing.Union[typing.List[str], None] = None
    database_username: typing.Union[str, None] = None
    database_password: typing.Union[str, None] = None
    database_tls_enabled: bool = False
    remote_path: str = ""
    capabilities: typing.Dict[str, Any] = Field(default_factory=dict)
    trade_ip: typing.Union[IPv4Address, None] = None
    trade_tcp_port: typing.Union[int, None] = Field(default=None, ge=1, le=65535)
    trade_udp_port: typing.Union[int, None] = Field(default=None, ge=1, le=65535)
    query_ip: typing.Union[IPv4Address, None] = None
    query_port: typing.Union[int, None] = Field(default=None, ge=1, le=65535)
    version_info: str = ""
    notes: str = ""
    is_enabled: bool = True

    @field_validator("trade_ip", "query_ip", mode="before")
    @classmethod
    def normalize_optional_ip(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_connection(self) -> "ResourceWrite":
        if self.resource_type == "rem":
            rem_config = (
                self.trade_ip, self.trade_tcp_port, self.trade_udp_port,
                self.query_ip, self.query_port,
            )
            if any(value is None for value in rem_config):
                raise ValueError("REM 交易与查询配置不能为空")
        else:
            self.trade_ip = None
            self.trade_tcp_port = None
            self.trade_udp_port = None
            self.query_ip = None
            self.query_port = None
        if self.resource_type != "database":
            if not self.host.strip() or not self.username.strip():
                raise ValueError("SSH 地址和用户名不能为空")
            if self.resource_type == "order":
                configured_actions = self.capabilities.get("order_actions")
                if configured_actions is None:
                    configured_actions = list(ORDER_ACTIONS)
                if (
                    not isinstance(configured_actions, list)
                    or not configured_actions
                    or any(action not in ORDER_ACTIONS for action in configured_actions)
                ):
                    raise ValueError("发单动作能力配置无效")
                self.capabilities = {
                    **self.capabilities,
                    "order_actions": list(dict.fromkeys(configured_actions)),
                }
            if self.resource_type == "parser":
                tool = str(self.capabilities.get("parser_tool") or "").strip()
                if tool not in PARSER_TOOLS:
                    raise ValueError("请选择受支持的解析工具")
                binary = tool
                config_filename = str(self.capabilities.get("parser_config_filename") or "").strip()
                if not config_filename:
                    config_filename = f"{binary[:-3] if binary.endswith('_v2') else binary}.xml"
                if not re.fullmatch(r"[A-Za-z0-9._-]+\.xml", config_filename):
                    raise ValueError("解析工具主配置文件必须以 .xml 结尾")
                self.capabilities = {
                    **self.capabilities,
                    "parser_tool": tool,
                    "parser_binary": binary,
                    "parser_config_filename": config_filename,
                    "parser_actions": self._parser_actions(),
                }
                if not self.remote_path.strip():
                    self.remote_path = f"/home/user0/{binary}"
            return self
        self.database_engine = self.database_engine or "mysql"
        self.database_connection_mode = self.database_connection_mode or "direct"
        self.database_port = self.database_port or 3306
        self.database_host = (self.database_host or "").strip()
        self.database_username = (self.database_username or "").strip()
        names = [name.strip() for name in (self.database_names or []) if name.strip()]
        self.database_names = list(dict.fromkeys(names))
        if not self.database_host or not self.database_username or not self.database_names:
            raise ValueError("数据库地址、用户名和至少一个数据库名称不能为空")
        if self.database_connection_mode == "ssh_tunnel" and (
            not self.host.strip() or not self.username.strip()
        ):
            raise ValueError("SSH 隧道地址和用户名不能为空")
        return self

    def _parser_actions(self) -> typing.List[str]:
        configured = self.capabilities.get("parser_actions")
        if configured is None:
            return list(PARSER_ACTIONS)
        if not isinstance(configured, list) or any(action not in PARSER_ACTIONS for action in configured):
            raise ValueError("解析指令能力配置无效")
        return list(dict.fromkeys(configured))


class ResourceOut(ORMModel):
    id: int
    name: str
    resource_type: str
    business_code: str
    host: str
    ssh_port: int
    username: str
    auth_type: str
    database_engine: typing.Union[str, None]
    database_connection_mode: typing.Union[str, None]
    database_host: typing.Union[str, None]
    database_port: typing.Union[int, None]
    database_names: typing.Union[typing.List[str], None]
    database_username: typing.Union[str, None]
    database_tls_enabled: bool
    has_database_password: bool
    remote_path: str
    capabilities: typing.Dict[str, Any]
    trade_ip: typing.Union[IPv4Address, None]
    trade_tcp_port: typing.Union[int, None]
    trade_udp_port: typing.Union[int, None]
    query_ip: typing.Union[IPv4Address, None]
    query_port: typing.Union[int, None]
    version_info: str
    notes: str
    is_enabled: bool
    health_status: str
    health_checked_at: typing.Union[datetime, None]
    created_at: datetime

    @model_validator(mode="after")
    def default_parser_actions(self) -> "ResourceOut":
        if self.resource_type == "parser" and "parser_actions" not in self.capabilities:
            self.capabilities = {**self.capabilities, "parser_actions": list(PARSER_ACTIONS)}
        return self


class ResourceConnectionTestRequest(BaseModel):
    resource_id: typing.Union[int, None] = Field(default=None, ge=1)
    resource_type: Literal["rem", "market", "order", "slnic", "capture", "coco", "parser"]
    host: str
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str
    auth_type: Literal["password", "private_key"] = "password"
    password: typing.Union[str, None] = None
    private_key: typing.Union[str, None] = None
    remote_path: str = ""
    capabilities: typing.Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ssh_connection(self) -> "ResourceConnectionTestRequest":
        self.host = self.host.strip()
        self.username = self.username.strip()
        if not self.host or not self.username:
            raise ValueError("SSH 地址和用户名不能为空")
        return self


class DatabaseDiscoveryRequest(BaseModel):
    resource_id: typing.Union[int, None] = Field(default=None, ge=1)
    database_connection_mode: Literal["direct", "ssh_tunnel"] = "direct"
    database_host: str
    database_port: int = Field(default=3306, ge=1, le=65535)
    database_username: str
    database_password: typing.Union[str, None] = None
    database_tls_enabled: bool = False
    host: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    auth_type: Literal["password", "private_key"] = "password"
    password: typing.Union[str, None] = None
    private_key: typing.Union[str, None] = None

    @model_validator(mode="after")
    def validate_discovery_connection(self) -> "DatabaseDiscoveryRequest":
        self.database_host = self.database_host.strip()
        self.database_username = self.database_username.strip()
        self.host = self.host.strip()
        self.username = self.username.strip()
        if not self.database_host or not self.database_username:
            raise ValueError("数据库地址和用户名不能为空")
        if self.database_connection_mode == "ssh_tunnel" and (not self.host or not self.username):
            raise ValueError("SSH 跳板机地址和用户名不能为空")
        return self


class DatabaseDiscoveryOut(BaseModel):
    databases: typing.List[str]
    filtered_system_count: int


class DatabaseSqlRequest(BaseModel):
    database_name: str
    sql: str = Field(min_length=1, max_length=100_000)


class DatabaseConfigItemOut(BaseModel):
    key: str
    description: typing.Union[str, None] = None


def _normalize_template_keys(keys: typing.List[str]) -> typing.List[str]:
    normalized = [key.strip() for key in keys]
    if any(not key or len(key) > 255 for key in normalized):
        raise ValueError("配置键不能为空且长度不能超过 255 个字符")
    if len(normalized) != len(set(normalized)):
        raise ValueError("配置键不能重复")
    return normalized


class DatabaseConfigTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keys: typing.List[str] = Field(min_length=1, max_length=1000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模板名称不能为空")
        if len(value.casefold()) > 128:
            raise ValueError("模板名称规范化后不能超过 128 个字符")
        return value

    @field_validator("keys")
    @classmethod
    def normalize_keys(cls, value: typing.List[str]) -> typing.List[str]:
        return _normalize_template_keys(value)


class DatabaseConfigTemplateRename(BaseModel):
    new_name: str = Field(min_length=1, max_length=128)

    @field_validator("new_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模板名称不能为空")
        if len(value.casefold()) > 128:
            raise ValueError("模板名称规范化后不能超过 128 个字符")
        return value


class DatabaseConfigTemplateOut(ORMModel):
    id: int
    name: str
    keys: typing.List[str]
    created_at: datetime
    updated_at: datetime


class OrderConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_name: str = Field(min_length=1, max_length=255)


class OrderConfigUpdate(BaseModel):
    content: str = Field(min_length=1)
    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class OrderConfigRename(BaseModel):
    new_name: str = Field(min_length=1, max_length=255)
    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class XmlAttributeOut(BaseModel):
    name: str
    value: str


class XmlNodeOut(BaseModel):
    type: Literal["element", "text", "comment", "cdata", "processing_instruction"]
    name: typing.Union[str, None] = None
    attributes: typing.List[XmlAttributeOut] = Field(default_factory=list)
    text: typing.Union[str, None] = None
    children: typing.List['XmlNodeOut'] = Field(default_factory=list)


class OrderConfigFileOut(BaseModel):
    name: str
    size: int
    modified_at: datetime


class OrderConfigListOut(BaseModel):
    tool: str
    directory: str
    files: typing.List[OrderConfigFileOut]


class OrderConfigDetailOut(OrderConfigFileOut):
    checksum: str
    content: str
    declaration: str
    document: XmlNodeOut
    tool: str


class StatisticsScriptFileOut(BaseModel):
    name: str
    size: int
    modified_at: datetime
    checksum: str
    executable: bool


class StatisticsScriptListOut(BaseModel):
    directory: str
    files: typing.List[StatisticsScriptFileOut]


class MarketScriptFileOut(StatisticsScriptFileOut):
    pass


class MarketScriptListOut(BaseModel):
    directory: str
    files: typing.List[MarketScriptFileOut]


class DatabaseExportRequest(DatabaseSqlRequest):
    format: Literal["csv", "xlsx"]


class DatabaseUpdateExecuteRequest(DatabaseSqlRequest):
    confirmation_id: str
    confirmation_text: str


class PlanDirectoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class PlanDirectoryOut(ORMModel):
    id: int
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class PlanWrite(BaseModel):
    directory_id: typing.Union[int, None] = None
    name: str
    business_code: Literal["fut_mm", "rem_two", "rem_two_mm"]
    description: str = ""
    default_resource_ids: typing.List[int] = Field(default_factory=list)
    config_version: str = "1.0"
    is_enabled: bool = True


class PlanOut(ORMModel):
    id: int
    directory_id: int
    name: str
    business_code: str
    description: str
    default_resource_ids: typing.List[int]
    config_version: str
    is_enabled: bool
    created_by: int
    created_at: datetime


class ScenarioWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: int
    name: str
    scenario_type: str
    config_version: str = "1.0"
    expected_artifacts: typing.List[str] = Field(default_factory=list)
    default_resource_ids: typing.Union[typing.List[int], None] = None
    required_resource_types: typing.List[str] = Field(default_factory=list)
    is_enabled: bool = True


class ScenarioOut(ORMModel):
    id: int
    plan_id: int
    name: str
    scenario_type: str
    config_version: str
    expected_artifacts: typing.List[str]
    default_resource_ids: typing.List[int]
    required_resource_types: typing.List[str]
    is_enabled: bool
    workflow_status: str
    draft_workflow_version_id: typing.Union[int, None]
    published_workflow_version_id: typing.Union[int, None]
    is_archived: bool
    created_at: datetime


class WorkflowNodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=128)


class ServerConfigNodeWrite(WorkflowNodeBase):
    node_type: Literal["server_config"]
    config: ServerConfig = Field(default_factory=ServerConfig)


class DatabaseConfigNodeWrite(WorkflowNodeBase):
    node_type: Literal["database_config"]
    config: DatabaseConfig = Field(default_factory=DatabaseConfig)


class WiringConfirmationNodeWrite(WorkflowNodeBase):
    node_type: Literal["wiring_confirmation"]
    config: WiringConfirmationConfig = Field(default_factory=WiringConfirmationConfig)


class RemStartupNodeWrite(WorkflowNodeBase):
    node_type: Literal["rem_startup"]
    config: RemStartupConfig = Field(default_factory=RemStartupConfig)


class MarketStartupNodeWrite(WorkflowNodeBase):
    node_type: Literal["market_startup"]
    config: MarketStartupConfig = Field(default_factory=MarketStartupConfig)


class OrderPreparationNodeWrite(WorkflowNodeBase):
    node_type: Literal["order_preparation"]
    config: OrderPreparationConfig = Field(default_factory=OrderPreparationConfig)


class SlnicStartNodeWrite(WorkflowNodeBase):
    node_type: Literal["slnic_start_capture"]
    config: SlnicStartConfig = Field(default_factory=SlnicStartConfig)


class SlnicStopNodeWrite(WorkflowNodeBase):
    node_type: Literal["slnic_stop_capture"]
    config: SlnicStopConfig = Field(default_factory=SlnicStopConfig)


class SlnicMergeNodeWrite(WorkflowNodeBase):
    node_type: Literal["slnic_merge_capture"]
    config: SlnicMergeConfig = Field(default_factory=SlnicMergeConfig)


class ParserNodeWrite(WorkflowNodeBase):
    node_type: Literal["parser_parse"]
    config: ParserConfig = Field(default_factory=ParserConfig)


class StatisticsNodeWrite(WorkflowNodeBase):
    node_type: Literal["data_statistics"]
    config: StatisticsConfig = Field(default_factory=StatisticsConfig)


class ReportGenerationNodeWrite(WorkflowNodeBase):
    node_type: Literal["report_generation"]
    config: ReportGenerationConfig = Field(default_factory=ReportGenerationConfig)


WorkflowNodeWrite = Annotated[
    typing.Union[
        ServerConfigNodeWrite,
        DatabaseConfigNodeWrite,
        WiringConfirmationNodeWrite,
        RemStartupNodeWrite,
        MarketStartupNodeWrite,
        OrderPreparationNodeWrite,
        SlnicStartNodeWrite,
        SlnicStopNodeWrite,
        SlnicMergeNodeWrite,
        ParserNodeWrite,
        StatisticsNodeWrite,
        ReportGenerationNodeWrite,
    ],
    Field(discriminator="node_type"),
]


class WorkflowDocumentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    resource_ids: typing.List[int] = Field(min_length=1)
    nodes: typing.List[WorkflowNodeWrite] = Field(default_factory=list)


class WorkflowVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_version_id: int = Field(gt=0)


class WorkflowNodeOutFields(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int


class ServerConfigNodeOut(ServerConfigNodeWrite, WorkflowNodeOutFields):
    pass


class DatabaseConfigNodeOut(DatabaseConfigNodeWrite, WorkflowNodeOutFields):
    pass


class WiringConfirmationNodeOut(WiringConfirmationNodeWrite, WorkflowNodeOutFields):
    pass


class RemStartupNodeOut(RemStartupNodeWrite, WorkflowNodeOutFields):
    pass


class MarketStartupNodeOut(MarketStartupNodeWrite, WorkflowNodeOutFields):
    pass


class OrderPreparationNodeOut(OrderPreparationNodeWrite, WorkflowNodeOutFields):
    pass


class SlnicStartNodeOut(SlnicStartNodeWrite, WorkflowNodeOutFields):
    pass


class SlnicStopNodeOut(SlnicStopNodeWrite, WorkflowNodeOutFields):
    pass


class SlnicMergeNodeOut(SlnicMergeNodeWrite, WorkflowNodeOutFields):
    pass


class ParserNodeOut(ParserNodeWrite, WorkflowNodeOutFields):
    pass


class StatisticsNodeOut(StatisticsNodeWrite, WorkflowNodeOutFields):
    pass


class ReportGenerationNodeOut(ReportGenerationNodeWrite, WorkflowNodeOutFields):
    pass


WorkflowNodeOut = Annotated[
    typing.Union[
        ServerConfigNodeOut,
        DatabaseConfigNodeOut,
        WiringConfirmationNodeOut,
        RemStartupNodeOut,
        MarketStartupNodeOut,
        OrderPreparationNodeOut,
        SlnicStartNodeOut,
        SlnicStopNodeOut,
        SlnicMergeNodeOut,
        ParserNodeOut,
        StatisticsNodeOut,
        ReportGenerationNodeOut,
    ],
    Field(discriminator="node_type"),
]


class WorkflowVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    version_no: int
    status: str
    revision: int
    resource_ids: typing.List[int]
    published_by: typing.Union[int, None]
    published_at: typing.Union[datetime, None]
    created_at: datetime
    updated_at: datetime
    nodes: typing.List[WorkflowNodeOut] = Field(default_factory=list)


class WorkflowDocumentOut(BaseModel):
    scenario: ScenarioOut
    draft: WorkflowVersionOut
    published_version_id: typing.Union[int, None]
    validation_errors: typing.List[typing.Dict[str, Any]] = Field(default_factory=list)


class CaptureItemOut(ORMModel):
    id: int
    item_key: str
    item_label: str
    item_description: typing.Union[str, None]
    value_text: typing.Union[str, None]
    source_reference: str
    raw_output: str
    exit_code: typing.Union[int, None]
    status: str
    error_message: typing.Union[str, None]


class CaptureSnapshotOut(ORMModel):
    id: int
    scope: str
    source_type: str
    resource_id: int
    database_name: typing.Union[str, None]
    status: str
    attempt: int
    error_message: typing.Union[str, None]
    started_at: datetime
    finished_at: typing.Union[datetime, None]
    items: typing.List[CaptureItemOut] = Field(default_factory=list)


class ContractDataFetchRequest(BaseModel):
    database_resource_id: int
    database_name: str
    contract_types: typing.List[Literal["futures", "options"]] = Field(min_length=1)


class ContractDataFileOut(ORMModel):
    id: int
    scenario_id: typing.Union[int, None]
    workflow_node_id: typing.Union[int, None]
    order_resource_id: int
    database_resource_id: typing.Union[int, None]
    database_name: typing.Union[str, None]
    contract_type: str
    source_table: str
    filename: str
    remote_path: str
    quote_date: typing.Union[str, None]
    row_count: int
    size: int
    checksum: str
    preview_rows: typing.List[typing.Dict[str, Any]]
    created_at: datetime


class RunCreate(BaseModel):
    plan_id: int
    scenario_id: int
    resource_ids: typing.List[int] = Field(min_length=1)
    timeout_minutes: typing.Union[int, None] = Field(default=None, ge=1, le=1440)


WiringInterfaceName = Annotated[str, Field(min_length=1, max_length=32)]


class WiringInterfaceNamesWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_interface_name: WiringInterfaceName
    client_interface_ip_address: IPv4Address
    market_interface_name: WiringInterfaceName
    market_interface_ip_address: IPv4Address
    auxiliary_interface_names: typing.List[WiringInterfaceName] = Field(
        default_factory=list, max_length=2
    )


class OrderRuntimeConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    xml_filename: str = Field(min_length=1, max_length=255)
    network_interface: str = Field(
        default="",
        max_length=15,
        pattern=r"^[A-Za-z0-9_.-]*$",
    )


class OrderActionRequest(BaseModel):
    action: OrderAction


class ParserTableExportRequest(BaseModel):
    table: Literal[
        "t_fut_orders",
        "t_fut_quotes",
        "t_fut_arbi_orders",
        "t_account_exchange_code",
    ]


class StatisticsInputSelectionRequest(BaseModel):
    relative_paths: typing.List[str] = Field(min_length=1)


class StatisticsRuntimeConfigRequest(StatisticsInputSelectionRequest):
    max_latency_ns: Annotated[int, Field(strict=True, ge=1)]


class StatisticsInputOut(BaseModel):
    relative_path: str
    filename: str
    source: Literal["root", "current_run"]
    size: int
    modified_at: datetime


class StatisticsCsvFilesOut(BaseModel):
    directory: str
    files: typing.List[StatisticsInputOut]


class StatisticsInputSelectionOut(BaseModel):
    inputs: typing.List[StatisticsInputOut]
    selected_by: int
    selected_at: datetime


class StatisticsRuntimeConfigOut(StatisticsInputSelectionOut):
    max_latency_ns: int = Field(ge=1)
    statistics_config_revision: int = Field(ge=0)
    changed: bool


class StatisticsAnalysisMetadataOut(BaseModel):
    analysis_no: int = Field(ge=1)
    status: Literal["running", "succeeded", "failed"]
    config_revision: int = Field(ge=0)
    inputs: typing.List[typing.Dict[str, Any]] = Field(default_factory=list)
    max_latency_ns: typing.Union[int, None] = Field(default=None, ge=1)
    script: typing.Dict[str, Any] = Field(default_factory=dict)
    reserved_at: str
    started_at: typing.Union[str, None] = None
    finished_at: typing.Union[str, None] = None
    duration_ms: typing.Union[int, None] = Field(default=None, ge=0)
    error_code: typing.Union[str, None] = None
    artifact_id: typing.Union[int, None] = None
    artifact_checksum: typing.Union[str, None] = None
    artifact_size: typing.Union[int, None] = Field(default=None, ge=0)


class StatisticsAnalysisDetailOut(BaseModel):
    analysis: StatisticsAnalysisMetadataOut
    artifact: typing.Dict[str, Any]


class StepOut(ORMModel):
    id: int
    code: str
    name: str
    workflow_node_id: typing.Union[int, None]
    node_type: str
    config_snapshot: typing.Dict[str, Any]
    result_summary: typing.Dict[str, Any]
    position: int
    status: StepStatus
    progress: int
    retry_count: int
    max_retries: int
    started_at: typing.Union[datetime, None]
    finished_at: typing.Union[datetime, None]
    duration_ms: typing.Union[int, None]
    error_message: typing.Union[str, None]


class MetricOut(ORMModel):
    id: int
    name: str
    value: float
    unit: str
    sample_count: typing.Union[int, None]
    detail: typing.Dict[str, Any]


class VerdictOut(ORMModel):
    id: int
    final_result: typing.Union[str, None]
    issue_description: str
    notes: str
    reviewed_by: typing.Union[int, None]
    reviewed_at: typing.Union[datetime, None]


class ArtifactOut(ORMModel):
    id: int
    step_id: typing.Union[int, None]
    artifact_type: str
    name: str
    content_type: str
    size: int
    checksum: str
    is_immutable: bool
    created_at: datetime


class ParserTableExportOut(BaseModel):
    table: Literal[
        "t_fut_orders",
        "t_fut_quotes",
        "t_fut_arbi_orders",
        "t_account_exchange_code",
    ]
    artifact_id: int
    filename: str
    database_name: str
    row_count: int
    size: int
    checksum: str
    source: Literal["manual", "auto"]
    exported_by: typing.Union[int, None]
    exported_at: datetime
    artifact: ArtifactOut


class RunStatusTransitionOut(ORMModel):
    id: int
    from_status: RunStatus
    to_status: RunStatus
    status_version: int
    source: str
    actor_id: typing.Union[int, None]
    reason: typing.Union[str, None]
    created_at: datetime


class RunOut(ORMModel):
    id: int
    run_number: str
    plan_id: int
    scenario_id: int
    workflow_version_id: typing.Union[int, None]
    business_code: str
    status: RunStatus
    status_version: int
    progress: int
    resource_ids: typing.List[int]
    config_snapshot: typing.Dict[str, Any]
    trace_id: str
    created_by: int
    started_at: typing.Union[datetime, None]
    finished_at: typing.Union[datetime, None]
    timeout_at: typing.Union[datetime, None]
    error_code: typing.Union[str, None]
    error_message: typing.Union[str, None]
    queue_reason: typing.Union[str, None]
    paused_from: typing.Union[str, None]
    logs_complete: bool
    created_at: datetime
    steps: typing.List[StepOut] = Field(default_factory=list)
    artifacts: typing.List[ArtifactOut] = Field(default_factory=list)
    metrics: typing.List[MetricOut] = Field(default_factory=list)
    verdict: typing.Union[VerdictOut, None] = None
    status_transitions: typing.List[RunStatusTransitionOut] = Field(default_factory=list)


class VerdictWrite(BaseModel):
    final_result: Literal["passed", "failed", "conditional"]
    issue_description: str = ""
    notes: str = ""


class LogOut(ORMModel):
    id: int
    event_id: typing.Union[str, None] = None
    log_type: str
    level: str
    event: str
    message: str
    trace_id: str
    user_id: typing.Union[int, None]
    run_id: typing.Union[int, None]
    step_id: typing.Union[int, None]
    source: str
    duration_ms: typing.Union[int, None] = None
    result: typing.Union[str, None] = None
    http_method: typing.Union[str, None] = None
    http_status: typing.Union[int, None] = None
    database_scope: typing.Union[str, None] = None
    sql_fingerprint: typing.Union[str, None] = None
    detail: typing.Dict[str, Any]
    is_redacted: bool
    created_at: datetime


class LogSummaryOut(ORMModel):
    id: int
    event_id: typing.Union[str, None]
    log_type: str
    level: str
    event: str
    message: str
    trace_id: str
    user_id: typing.Union[int, None]
    run_id: typing.Union[int, None]
    step_id: typing.Union[int, None]
    source: str
    duration_ms: typing.Union[int, None]
    result: typing.Union[str, None]
    http_method: typing.Union[str, None]
    http_status: typing.Union[int, None]
    database_scope: typing.Union[str, None]
    sql_fingerprint: typing.Union[str, None]
    created_at: datetime


class LogSearchPage(BaseModel):
    items: typing.List[LogSummaryOut]
    total: int
    page: int
    page_size: int


class LogDetailOut(BaseModel):
    summary: LogSummaryOut
    payload: typing.Dict[str, Any]


class AuditOut(ORMModel):
    id: int
    actor_id: typing.Union[int, None]
    action: str
    object_type: str
    object_id: typing.Union[str, None]
    result: str
    source_ip: typing.Union[str, None]
    trace_id: str
    detail: typing.Dict[str, Any]
    created_at: datetime
