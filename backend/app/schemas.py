from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    display_name: str | None = None
    role: Literal["admin", "tester", "visitor"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserOut(ORMModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class ResourceWrite(BaseModel):
    name: str
    resource_type: Literal["rem", "market", "order", "slnic", "capture", "coco", "database"]
    business_code: Literal["fut_mm", "rem_two", "rem_two_mm"]
    host: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    auth_type: Literal["password", "private_key"] = "password"
    password: str | None = None
    private_key: str | None = None
    database_engine: Literal["mysql"] | None = None
    database_connection_mode: Literal["direct", "ssh_tunnel"] | None = None
    database_host: str | None = None
    database_port: int | None = Field(default=None, ge=1, le=65535)
    database_names: list[str] | None = None
    database_username: str | None = None
    database_password: str | None = None
    database_tls_enabled: bool = False
    remote_path: str = ""
    capabilities: dict[str, Any] = Field(default_factory=dict)
    version_info: str = ""
    notes: str = ""
    is_enabled: bool = True

    @model_validator(mode="after")
    def validate_connection(self) -> "ResourceWrite":
        if self.resource_type != "database":
            if not self.host.strip() or not self.username.strip():
                raise ValueError("SSH 地址和用户名不能为空")
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


class ResourceOut(ORMModel):
    id: int
    name: str
    resource_type: str
    business_code: str
    host: str
    ssh_port: int
    username: str
    auth_type: str
    database_engine: str | None
    database_connection_mode: str | None
    database_host: str | None
    database_port: int | None
    database_names: list[str] | None
    database_username: str | None
    database_tls_enabled: bool
    has_database_password: bool
    remote_path: str
    capabilities: dict[str, Any]
    version_info: str
    notes: str
    is_enabled: bool
    health_status: str
    health_checked_at: datetime | None
    created_at: datetime


class DatabaseDiscoveryRequest(BaseModel):
    resource_id: int | None = Field(default=None, ge=1)
    database_connection_mode: Literal["direct", "ssh_tunnel"] = "direct"
    database_host: str
    database_port: int = Field(default=3306, ge=1, le=65535)
    database_username: str
    database_password: str | None = None
    database_tls_enabled: bool = False
    host: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = ""
    auth_type: Literal["password", "private_key"] = "password"
    password: str | None = None
    private_key: str | None = None

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
    databases: list[str]
    simulated: bool
    filtered_system_count: int


class DatabaseSqlRequest(BaseModel):
    database_name: str
    sql: str = Field(min_length=1, max_length=100_000)


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
    name: str | None = None
    attributes: list[XmlAttributeOut] = Field(default_factory=list)
    text: str | None = None
    children: list["XmlNodeOut"] = Field(default_factory=list)


class OrderConfigFileOut(BaseModel):
    name: str
    size: int
    modified_at: datetime


class OrderConfigListOut(BaseModel):
    tool: str
    directory: str
    simulated: bool
    files: list[OrderConfigFileOut]


class OrderConfigDetailOut(OrderConfigFileOut):
    checksum: str
    content: str
    declaration: str
    document: XmlNodeOut
    tool: str
    simulated: bool


class DatabaseExportRequest(DatabaseSqlRequest):
    format: Literal["csv", "xlsx"]


class DatabaseUpdateExecuteRequest(DatabaseSqlRequest):
    confirmation_id: str
    confirmation_text: str


class PlanWrite(BaseModel):
    name: str
    business_code: Literal["fut_mm", "rem_two", "rem_two_mm"]
    description: str = ""
    default_resource_ids: list[int] = Field(default_factory=list)
    config_version: str = "1.0"
    is_enabled: bool = True


class PlanOut(ORMModel):
    id: int
    name: str
    business_code: str
    description: str
    default_resource_ids: list[int]
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
    expected_artifacts: list[str] = Field(default_factory=list)
    default_resource_ids: list[int] | None = None
    required_resource_types: list[str] = Field(default_factory=list)
    is_enabled: bool = True


class ScenarioOut(ORMModel):
    id: int
    plan_id: int
    name: str
    scenario_type: str
    config_version: str
    expected_artifacts: list[str]
    default_resource_ids: list[int]
    required_resource_types: list[str]
    is_enabled: bool
    created_at: datetime


class RunCreate(BaseModel):
    plan_id: int
    scenario_id: int
    resource_ids: list[int] = Field(min_length=1)
    timeout_minutes: int = Field(default=120, ge=5, le=1440)


class StepOut(ORMModel):
    id: int
    code: str
    name: str
    position: int
    status: str
    progress: int
    retry_count: int
    max_retries: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error_message: str | None


class MetricOut(ORMModel):
    id: int
    name: str
    value: float
    unit: str
    sample_count: int | None
    detail: dict[str, Any]


class VerdictOut(ORMModel):
    id: int
    final_result: str | None
    issue_description: str
    notes: str
    reviewed_by: int | None
    reviewed_at: datetime | None


class ArtifactOut(ORMModel):
    id: int
    step_id: int | None
    artifact_type: str
    name: str
    content_type: str
    size: int
    checksum: str
    is_immutable: bool
    created_at: datetime


class RunOut(ORMModel):
    id: int
    run_number: str
    plan_id: int
    scenario_id: int
    business_code: str
    status: str
    progress: int
    resource_ids: list[int]
    config_snapshot: dict[str, Any]
    trace_id: str
    created_by: int
    started_at: datetime | None
    finished_at: datetime | None
    timeout_at: datetime | None
    error_code: str | None
    error_message: str | None
    queue_reason: str | None
    paused_from: str | None
    logs_complete: bool
    created_at: datetime
    steps: list[StepOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    metrics: list[MetricOut] = Field(default_factory=list)
    verdict: VerdictOut | None = None


class VerdictWrite(BaseModel):
    final_result: Literal["passed", "failed", "conditional"]
    issue_description: str = ""
    notes: str = ""


class LogOut(ORMModel):
    id: int
    log_type: str
    level: str
    event: str
    message: str
    trace_id: str
    user_id: int | None
    run_id: int | None
    step_id: int | None
    source: str
    detail: dict[str, Any]
    is_redacted: bool
    created_at: datetime


class AuditOut(ORMModel):
    id: int
    actor_id: int | None
    action: str
    object_type: str
    object_id: str | None
    result: str
    source_ip: str | None
    trace_id: str
    detail: dict[str, Any]
    created_at: datetime
