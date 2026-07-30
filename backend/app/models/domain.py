from __future__ import annotations

import typing
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import beijing_now
from app.core.types import BeijingDateTime, JSONText


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    updated_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now, onupdate=beijing_now)


class User(TimestampMixin, Base):
    __tablename__ = "t_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="visitor", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    refresh_tokens: Mapped[typing.List['RefreshToken']] = relationship(back_populates="user", cascade="all, delete-orphan")
    database_config_templates: Mapped[typing.List['DatabaseConfigTemplate']] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class DatabaseConfigTemplate(TimestampMixin, Base):
    __tablename__ = "t_database_config_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_name", name="uq_database_config_template_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("t_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    keys: Mapped[typing.List[str]] = mapped_column(JSONText(), default=list)

    user: Mapped['User'] = relationship(back_populates="database_config_templates")


class RefreshToken(Base):
    __tablename__ = "t_refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("t_users.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(BeijingDateTime())
    revoked_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class BusinessType(TimestampMixin, Base):
    __tablename__ = "t_business_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Resource(TimestampMixin, Base):
    __tablename__ = "t_resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    business_code: Mapped[str] = mapped_column(String(32), index=True)
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128))
    auth_type: Mapped[str] = mapped_column(String(16), default="password")
    encrypted_password: Mapped[typing.Union[str, None]] = mapped_column(Text)
    encrypted_private_key: Mapped[typing.Union[str, None]] = mapped_column(Text)
    database_engine: Mapped[typing.Union[str, None]] = mapped_column(String(32))
    database_connection_mode: Mapped[typing.Union[str, None]] = mapped_column(String(32))
    database_host: Mapped[typing.Union[str, None]] = mapped_column(String(255))
    database_port: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    database_names: Mapped[typing.Union[typing.List[str], None]] = mapped_column(JSONText)
    database_username: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    encrypted_database_password: Mapped[typing.Union[str, None]] = mapped_column(Text)
    database_tls_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_path: Mapped[str] = mapped_column(String(512), default="")
    capabilities: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    trade_ip: Mapped[typing.Union[str, None]] = mapped_column(String(45))
    trade_tcp_port: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    trade_udp_port: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    query_ip: Mapped[typing.Union[str, None]] = mapped_column(String(45))
    query_port: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    version_info: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown")
    health_checked_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    locks: Mapped[typing.List['ResourceLock']] = relationship(back_populates="resource")

    @property
    def has_database_password(self) -> bool:
        return bool(self.encrypted_database_password)


class DatabaseUpdateConfirmation(Base):
    __tablename__ = "t_database_update_confirmations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("t_users.id", ondelete="CASCADE"), index=True)
    database_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(255))
    sql_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    estimated_rows: Mapped[int] = mapped_column(Integer)
    actual_rows: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(BeijingDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    completed_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())


class PlanDirectory(TimestampMixin, Base):
    __tablename__ = "t_plan_directories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plans: Mapped[typing.List['TestPlan']] = relationship(back_populates="directory")


class TestPlan(TimestampMixin, Base):
    __tablename__ = "t_test_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    directory_id: Mapped[int] = mapped_column(
        ForeignKey("t_plan_directories.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), index=True)
    business_code: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    default_resource_ids: Mapped[typing.List[int]] = mapped_column(JSONText, default=list)
    config_version: Mapped[str] = mapped_column(String(64), default="1.0")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("t_users.id"))
    directory: Mapped[PlanDirectory] = relationship(back_populates="plans")
    scenarios: Mapped[typing.List['TestScenario']] = relationship(back_populates="plan", cascade="all, delete-orphan")
    resource_links: Mapped[typing.List['PlanResource']] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanResource.position",
    )


class TestScenario(TimestampMixin, Base):
    __tablename__ = "t_test_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("t_test_plans.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    scenario_type: Mapped[str] = mapped_column(String(64), index=True)
    config_version: Mapped[str] = mapped_column(String(64), default="1.0")
    expected_artifacts: Mapped[typing.List[str]] = mapped_column(JSONText, default=list)
    default_resource_ids: Mapped[typing.List[int]] = mapped_column(JSONText, default=list)
    required_resource_types: Mapped[typing.List[str]] = mapped_column(JSONText, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    workflow_status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    draft_workflow_version_id: Mapped[typing.Union[int, None]] = mapped_column(
        ForeignKey(
            "t_scenario_workflow_versions.id",
            name="fk_test_scenarios_draft_workflow_version_id",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    published_workflow_version_id: Mapped[typing.Union[int, None]] = mapped_column(
        ForeignKey(
            "t_scenario_workflow_versions.id",
            name="fk_test_scenarios_published_workflow_version_id",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plan: Mapped[TestPlan] = relationship(back_populates="scenarios")
    workflow_versions: Mapped[typing.List['ScenarioWorkflowVersion']] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        foreign_keys="ScenarioWorkflowVersion.scenario_id",
    )
    resource_links: Mapped[typing.List['ScenarioResource']] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioResource.position",
    )


class ScenarioWorkflowVersion(TimestampMixin, Base):
    __tablename__ = "t_scenario_workflow_versions"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id",
            "version_no",
            "generation_no",
            name="uq_scenario_workflow_version_generation",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("t_test_scenarios.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    generation_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    resource_ids: Mapped[typing.List[int]] = mapped_column(JSONText, default=list)
    created_by: Mapped[int] = mapped_column(ForeignKey("t_users.id"), index=True)
    published_by: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_users.id"))
    published_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    scenario: Mapped[TestScenario] = relationship(
        back_populates="workflow_versions", foreign_keys=[scenario_id]
    )
    nodes: Mapped[typing.List['ScenarioWorkflowNode']] = relationship(
        back_populates="workflow_version", cascade="all, delete-orphan", order_by="ScenarioWorkflowNode.position"
    )
    resource_links: Mapped[typing.List['WorkflowVersionResource']] = relationship(
        back_populates="workflow_version",
        cascade="all, delete-orphan",
        order_by="WorkflowVersionResource.position",
    )


class ScenarioWorkflowNode(TimestampMixin, Base):
    __tablename__ = "t_scenario_workflow_nodes"
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "node_key", name="uq_workflow_node_key"),
        UniqueConstraint("workflow_version_id", "position", name="uq_workflow_node_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_version_id: Mapped[int] = mapped_column(ForeignKey("t_scenario_workflow_versions.id", ondelete="CASCADE"), index=True)
    node_key: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer)
    node_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    workflow_version: Mapped[ScenarioWorkflowVersion] = relationship(back_populates="nodes")
    contract_file_links: Mapped[typing.List['WorkflowNodeContractFile']] = relationship(
        back_populates="workflow_node",
        cascade="all, delete-orphan",
        order_by="WorkflowNodeContractFile.position",
    )


class TestRun(TimestampMixin, Base):
    __tablename__ = "t_test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("t_test_plans.id"), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("t_test_scenarios.id"), index=True)
    workflow_version_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_scenario_workflow_versions.id"), index=True)
    business_code: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    status_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    resource_ids: Mapped[typing.List[int]] = mapped_column(JSONText, default=list)
    config_snapshot: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("t_users.id"), index=True)
    started_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    timeout_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    error_code: Mapped[typing.Union[str, None]] = mapped_column(String(64))
    error_message: Mapped[typing.Union[str, None]] = mapped_column(Text)
    queue_reason: Mapped[typing.Union[str, None]] = mapped_column(Text)
    paused_from: Mapped[typing.Union[str, None]] = mapped_column(String(40))
    logs_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    steps: Mapped[typing.List['RunStep']] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="RunStep.position")
    artifacts: Mapped[typing.List['Artifact']] = relationship(back_populates="run", cascade="all, delete-orphan")
    metrics: Mapped[typing.List['Metric']] = relationship(back_populates="run", cascade="all, delete-orphan")
    verdict: Mapped["Verdict | None"] = relationship(back_populates="run", cascade="all, delete-orphan", uselist=False)
    locks: Mapped[typing.List['ResourceLock']] = relationship(back_populates="run")
    resource_links: Mapped[typing.List['RunResource']] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunResource.position",
    )
    status_transitions: Mapped[typing.List['RunStatusTransition']] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunStatusTransition.id",
    )
    __mapper_args__ = {
        "version_id_col": status_version,
        "version_id_generator": False,
    }


class RunStatusTransition(Base):
    __tablename__ = "t_run_status_transitions"
    __table_args__ = (
        UniqueConstraint("run_id", "status_version", name="uq_run_status_transition_version"),
        Index("ix_run_status_transition_created", "run_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40))
    status_version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64), default="service", index=True)
    actor_id: Mapped[typing.Union[int, None]] = mapped_column(
        ForeignKey("t_users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[typing.Union[str, None]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        BeijingDateTime(), default=beijing_now, index=True
    )
    run: Mapped[TestRun] = relationship(back_populates="status_transitions")


class DurableTask(Base):
    __tablename__ = "t_durable_tasks"
    __table_args__ = (
        Index("ix_durable_task_dispatch", "status", "available_at", "lease_expires_at"),
        Index(
            "ix_t_durable_tasks_idempotency_key",
            "idempotency_key",
            unique=True,
            mysql_length=191,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now, index=True)
    lease_expires_at: Mapped[typing.Union[datetime, None]] = mapped_column(
        BeijingDateTime(), index=True
    )
    locked_by: Mapped[typing.Union[str, None]] = mapped_column(String(128), index=True)
    last_error: Mapped[typing.Union[str, None]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    started_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())


class ScenarioResource(Base):
    __tablename__ = "t_scenario_resources"
    __table_args__ = (
        UniqueConstraint("scenario_id", "resource_id", name="uq_scenario_resource"),
        UniqueConstraint("scenario_id", "position", name="uq_scenario_resource_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("t_test_scenarios.id", ondelete="CASCADE"), index=True
    )
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[TestScenario] = relationship(back_populates="resource_links")


class PlanResource(Base):
    __tablename__ = "t_plan_resources"
    __table_args__ = (
        UniqueConstraint("plan_id", "resource_id", name="uq_plan_resource"),
        UniqueConstraint("plan_id", "position", name="uq_plan_resource_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("t_test_plans.id", ondelete="CASCADE"), index=True
    )
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    plan: Mapped[TestPlan] = relationship(back_populates="resource_links")


class WorkflowVersionResource(Base):
    __tablename__ = "t_workflow_version_resources"
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "resource_id", name="uq_workflow_version_resource"),
        UniqueConstraint("workflow_version_id", "position", name="uq_workflow_version_resource_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_version_id: Mapped[int] = mapped_column(
        ForeignKey("t_scenario_workflow_versions.id", ondelete="CASCADE"), index=True
    )
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    workflow_version: Mapped[ScenarioWorkflowVersion] = relationship(back_populates="resource_links")


class RunResource(Base):
    __tablename__ = "t_run_resources"
    __table_args__ = (
        UniqueConstraint("run_id", "resource_id", name="uq_run_resource"),
        UniqueConstraint("run_id", "position", name="uq_run_resource_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    run: Mapped[TestRun] = relationship(back_populates="resource_links")


class WorkflowNodeContractFile(Base):
    __tablename__ = "t_workflow_node_contract_files"
    __table_args__ = (
        UniqueConstraint("workflow_node_id", "contract_file_id", name="uq_workflow_node_contract_file"),
        UniqueConstraint("workflow_node_id", "position", name="uq_workflow_node_contract_position"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_node_id: Mapped[int] = mapped_column(
        ForeignKey("t_scenario_workflow_nodes.id", ondelete="CASCADE"), index=True
    )
    contract_file_id: Mapped[int] = mapped_column(
        ForeignKey("t_contract_data_files.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    workflow_node: Mapped[ScenarioWorkflowNode] = relationship(back_populates="contract_file_links")


class RunStep(Base):
    __tablename__ = "t_run_steps"
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_run_step_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    workflow_node_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_scenario_workflow_nodes.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    node_type: Mapped[str] = mapped_column(String(40), default="legacy")
    config_snapshot: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    result_summary: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    started_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    duration_ms: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    error_message: Mapped[typing.Union[str, None]] = mapped_column(Text)
    run: Mapped[TestRun] = relationship(back_populates="steps")


class ConfigurationCaptureSnapshot(Base):
    __tablename__ = "t_configuration_capture_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("t_test_scenarios.id", ondelete="CASCADE"), index=True)
    workflow_version_id: Mapped[int] = mapped_column(ForeignKey("t_scenario_workflow_versions.id", ondelete="CASCADE"), index=True)
    workflow_node_id: Mapped[int] = mapped_column(ForeignKey("t_scenario_workflow_nodes.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    run_step_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_run_steps.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    source_type: Mapped[str] = mapped_column(String(24), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    database_name: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_message: Mapped[typing.Union[str, None]] = mapped_column(Text)
    created_by: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_users.id"))
    started_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    items: Mapped[typing.List['ConfigurationCaptureItem']] = relationship(
        cascade="all, delete-orphan", order_by="ConfigurationCaptureItem.id"
    )


class ConfigurationCaptureItem(Base):
    __tablename__ = "t_configuration_capture_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("t_configuration_capture_snapshots.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(128), index=True)
    item_label: Mapped[str] = mapped_column(String(128))
    value_text: Mapped[typing.Union[str, None]] = mapped_column(Text)
    source_reference: Mapped[str] = mapped_column(Text, default="")
    raw_output: Mapped[str] = mapped_column(Text, default="")
    exit_code: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="succeeded", index=True)
    error_message: Mapped[typing.Union[str, None]] = mapped_column(Text)


class ContractDataFile(Base):
    __tablename__ = "t_contract_data_files"
    __table_args__ = (
        Index(
            "uq_t_contract_data_files_node_name_checksum",
            "workflow_node_id",
            "filename",
            "checksum",
            unique=True,
            mysql_length={"filename": 120, "checksum": 64},
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[typing.Union[int, None]] = mapped_column(
        ForeignKey("t_test_scenarios.id", ondelete="SET NULL"), index=True
    )
    workflow_node_id: Mapped[typing.Union[int, None]] = mapped_column(
        ForeignKey("t_scenario_workflow_nodes.id", ondelete="SET NULL"), index=True
    )
    order_resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    database_resource_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_resources.id"), index=True)
    database_name: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    contract_type: Mapped[str] = mapped_column(String(16), index=True)
    source_table: Mapped[str] = mapped_column(String(128), default="")
    filename: Mapped[str] = mapped_column(String(255))
    remote_path: Mapped[str] = mapped_column(String(1024))
    archive_path: Mapped[str] = mapped_column(String(1024))
    quote_date: Mapped[typing.Union[str, None]] = mapped_column(String(32), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    preview_rows: Mapped[typing.List[typing.Dict[str, Any]]] = mapped_column(JSONText, default=list)
    created_by: Mapped[int] = mapped_column(ForeignKey("t_users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)


class LogRecord(Base):
    __tablename__ = "t_log_records"
    __table_args__ = (
        Index("ix_t_log_records_run_created", "run_id", "created_at"),
        Index("ix_t_log_records_trace_created", "trace_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[typing.Union[str, None]] = mapped_column(String(64), unique=True, index=True)
    log_type: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    event: Mapped[str] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_users.id"), index=True)
    run_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_run_steps.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="api", index=True)
    duration_ms: Mapped[typing.Union[int, None]] = mapped_column(Integer, index=True)
    result: Mapped[typing.Union[str, None]] = mapped_column(String(32), index=True)
    http_method: Mapped[typing.Union[str, None]] = mapped_column(String(16), index=True)
    http_status: Mapped[typing.Union[int, None]] = mapped_column(Integer, index=True)
    database_scope: Mapped[typing.Union[str, None]] = mapped_column(String(32), index=True)
    sql_fingerprint: Mapped[typing.Union[str, None]] = mapped_column(String(64), index=True)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    artifact_path: Mapped[typing.Union[str, None]] = mapped_column(String(1024))
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now, index=True)


class Artifact(Base):
    __tablename__ = "t_artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_run_steps.id", ondelete="SET NULL"))
    artifact_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    run: Mapped[TestRun] = relationship(back_populates="artifacts")


class Metric(Base):
    __tablename__ = "t_metrics"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_run_metric_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="us")
    sample_count: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    run: Mapped[TestRun] = relationship(back_populates="metrics")


class Verdict(TimestampMixin, Base):
    __tablename__ = "t_verdicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), unique=True)
    final_result: Mapped[typing.Union[str, None]] = mapped_column(String(32))
    issue_description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_users.id"))
    reviewed_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime())
    run: Mapped[TestRun] = relationship(back_populates="verdict")


class ResourceLock(Base):
    __tablename__ = "t_resource_locks"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("t_resources.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("t_test_runs.id", ondelete="CASCADE"), index=True)
    acquired_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now)
    lease_expires_at: Mapped[datetime] = mapped_column(BeijingDateTime(), index=True)
    released_at: Mapped[typing.Union[datetime, None]] = mapped_column(BeijingDateTime(), index=True)
    release_reason: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    resource: Mapped[Resource] = relationship(back_populates="locks")
    run: Mapped[TestRun] = relationship(back_populates="locks")


class AuditLog(Base):
    __tablename__ = "t_audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("t_users.id"), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    object_type: Mapped[str] = mapped_column(String(64), index=True)
    object_id: Mapped[typing.Union[str, None]] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32), default="success")
    source_ip: Mapped[typing.Union[str, None]] = mapped_column(String(64))
    user_agent: Mapped[typing.Union[str, None]] = mapped_column(String(512))
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSONText, default=dict)
    created_at: Mapped[datetime] = mapped_column(BeijingDateTime(), default=beijing_now, index=True)
