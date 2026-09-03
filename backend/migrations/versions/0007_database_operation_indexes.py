"""Add database operation performance indexes.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import json
import typing

import sqlalchemy as sa
from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _payload_run_id(payload: typing.Any) -> typing.Optional[int]:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or payload.get("run_id") is None:
        return None
    try:
        return int(payload["run_id"])
    except (TypeError, ValueError):
        return None


def _backfill_task_run_ids() -> None:
    context = op.get_context()
    if getattr(context, "as_sql", False):
        return
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, payload FROM t_durable_tasks "
            "WHERE payload LIKE :run_id_pattern"
        ),
        {"run_id_pattern": '%"run_id"%'},
    ).all()
    existing_run_ids = set(
        connection.execute(sa.text("SELECT id FROM t_test_runs")).scalars().all()
    )
    updates = [
        {"task_id": task_id, "run_id": run_id}
        for task_id, payload in rows
        if (run_id := _payload_run_id(payload)) is not None
        and run_id in existing_run_ids
    ]
    if updates:
        connection.execute(
            sa.text(
                "UPDATE t_durable_tasks SET run_id = :run_id WHERE id = :task_id"
            ),
            updates,
        )


def upgrade() -> None:
    context = op.get_context()
    inspector = None if getattr(context, "as_sql", False) else sa.inspect(op.get_bind())
    durable_columns = (
        {column["name"] for column in inspector.get_columns("t_durable_tasks")}
        if inspector
        else set()
    )
    durable_foreign_keys = inspector.get_foreign_keys("t_durable_tasks") if inspector else []
    existing_indexes = (
        {
            table_name: {index["name"] for index in inspector.get_indexes(table_name)}
            for table_name in (
                "t_durable_tasks",
                "t_log_records",
                "t_audit_logs",
                "t_resource_locks",
            )
        }
        if inspector
        else {}
    )

    with op.batch_alter_table("t_durable_tasks") as batch:
        if "run_id" not in durable_columns:
            batch.add_column(sa.Column("run_id", sa.Integer(), nullable=True))
        if not any(
            foreign_key["constrained_columns"] == ["run_id"]
            and foreign_key["referred_table"] == "t_test_runs"
            for foreign_key in durable_foreign_keys
        ):
            batch.create_foreign_key(
                "fk_durable_tasks_run_id",
                "t_test_runs",
                ["run_id"],
                ["id"],
                ondelete="CASCADE",
            )

    _backfill_task_run_ids()

    indexes = (
        ("ix_t_durable_tasks_run_id", "t_durable_tasks", ["run_id"]),
        (
            "ix_t_durable_tasks_task_status_run",
            "t_durable_tasks",
            ["task_type", "status", "run_id"],
        ),
        (
            "ix_t_log_records_log_type_created",
            "t_log_records",
            ["log_type", "created_at"],
        ),
        (
            "ix_t_log_records_database_scope_created",
            "t_log_records",
            ["database_scope", "created_at"],
        ),
        (
            "ix_t_log_records_sql_fingerprint_created",
            "t_log_records",
            ["sql_fingerprint", "created_at"],
        ),
        (
            "ix_t_log_records_result_created",
            "t_log_records",
            ["result", "created_at"],
        ),
        (
            "ix_t_audit_logs_action_created",
            "t_audit_logs",
            ["action", "created_at"],
        ),
        (
            "ix_t_audit_logs_object_type_created",
            "t_audit_logs",
            ["object_type", "created_at"],
        ),
        (
            "ix_t_resource_locks_active_resource",
            "t_resource_locks",
            ["resource_id", "released_at"],
        ),
    )
    for name, table_name, columns in indexes:
        if name not in existing_indexes.get(table_name, set()):
            op.create_index(name, table_name, columns)


def downgrade() -> None:
    op.drop_index("ix_t_resource_locks_active_resource", table_name="t_resource_locks")
    op.drop_index("ix_t_audit_logs_object_type_created", table_name="t_audit_logs")
    op.drop_index("ix_t_audit_logs_action_created", table_name="t_audit_logs")
    op.drop_index("ix_t_log_records_result_created", table_name="t_log_records")
    op.drop_index("ix_t_log_records_sql_fingerprint_created", table_name="t_log_records")
    op.drop_index("ix_t_log_records_database_scope_created", table_name="t_log_records")
    op.drop_index("ix_t_log_records_log_type_created", table_name="t_log_records")
    op.drop_index("ix_t_durable_tasks_task_status_run", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_run_id", table_name="t_durable_tasks")

    with op.batch_alter_table("t_durable_tasks") as batch:
        batch.drop_constraint("fk_durable_tasks_run_id", type_="foreignkey")
        batch.drop_column("run_id")
