from __future__ import annotations

import typing
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="visitor", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    refresh_tokens: Mapped[typing.List['RefreshToken']] = relationship(back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class BusinessType(TimestampMixin, Base):
    __tablename__ = "business_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Resource(TimestampMixin, Base):
    __tablename__ = "resources"
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
    database_names: Mapped[typing.Union[typing.List[str], None]] = mapped_column(JSON)
    database_username: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    encrypted_database_password: Mapped[typing.Union[str, None]] = mapped_column(Text)
    database_tls_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_path: Mapped[str] = mapped_column(String(512), default="")
    capabilities: Mapped[typing.Dict[str, Any]] = mapped_column(JSON, default=dict)
    version_info: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    health_status: Mapped[str] = mapped_column(String(32), default="unknown")
    health_checked_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    locks: Mapped[typing.List['ResourceLock']] = relationship(back_populates="resource")

    @property
    def has_database_password(self) -> bool:
        return bool(self.encrypted_database_password)


class DatabaseUpdateConfirmation(Base):
    __tablename__ = "database_update_confirmations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    database_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(255))
    sql_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    estimated_rows: Mapped[int] = mapped_column(Integer)
    actual_rows: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))


class TestPlan(TimestampMixin, Base):
    __tablename__ = "test_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    business_code: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    default_resource_ids: Mapped[typing.List[int]] = mapped_column(JSON, default=list)
    config_version: Mapped[str] = mapped_column(String(64), default="1.0")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scenarios: Mapped[typing.List['TestScenario']] = relationship(back_populates="plan", cascade="all, delete-orphan")


class TestScenario(TimestampMixin, Base):
    __tablename__ = "test_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    scenario_type: Mapped[str] = mapped_column(String(64), index=True)
    config_version: Mapped[str] = mapped_column(String(64), default="1.0")
    expected_artifacts: Mapped[typing.List[str]] = mapped_column(JSON, default=list)
    default_resource_ids: Mapped[typing.List[int]] = mapped_column(JSON, default=list)
    required_resource_types: Mapped[typing.List[str]] = mapped_column(JSON, default=list)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    plan: Mapped[TestPlan] = relationship(back_populates="scenarios")


class TestRun(TimestampMixin, Base):
    __tablename__ = "test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("test_plans.id"), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("test_scenarios.id"), index=True)
    business_code: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    resource_ids: Mapped[typing.List[int]] = mapped_column(JSON, default=list)
    config_snapshot: Mapped[typing.Dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    timeout_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
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


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_run_step_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    started_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    error_message: Mapped[typing.Union[str, None]] = mapped_column(Text)
    run: Mapped[TestRun] = relationship(back_populates="steps")


class LogRecord(Base):
    __tablename__ = "log_records"
    __table_args__ = (
        Index("ix_logs_run_created", "run_id", "created_at"),
        Index("ix_logs_trace_created", "trace_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    log_type: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    event: Mapped[str] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("run_steps.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(64), default="api", index=True)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[typing.Union[str, None]] = mapped_column(String(1024))
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    step_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("run_steps.id", ondelete="SET NULL"))
    artifact_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run: Mapped[TestRun] = relationship(back_populates="artifacts")


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("run_id", "name", name="uq_run_metric_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="us")
    sample_count: Mapped[typing.Union[int, None]] = mapped_column(Integer)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSON, default=dict)
    run: Mapped[TestRun] = relationship(back_populates="metrics")


class Verdict(TimestampMixin, Base):
    __tablename__ = "verdicts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), unique=True)
    final_result: Mapped[typing.Union[str, None]] = mapped_column(String(32))
    issue_description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True))
    run: Mapped[TestRun] = relationship(back_populates="verdict")


class ResourceLock(Base):
    __tablename__ = "resource_locks"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[typing.Union[datetime, None]] = mapped_column(DateTime(timezone=True), index=True)
    release_reason: Mapped[typing.Union[str, None]] = mapped_column(String(128))
    resource: Mapped[Resource] = relationship(back_populates="locks")
    run: Mapped[TestRun] = relationship(back_populates="locks")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[typing.Union[int, None]] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    object_type: Mapped[str] = mapped_column(String(64), index=True)
    object_id: Mapped[typing.Union[str, None]] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32), default="success")
    source_ip: Mapped[typing.Union[str, None]] = mapped_column(String(64))
    user_agent: Mapped[typing.Union[str, None]] = mapped_column(String(512))
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[typing.Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
