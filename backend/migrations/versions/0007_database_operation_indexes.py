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
    with op.batch_alter_table("t_durable_tasks") as batch:
        batch.add_column(sa.Column("run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_durable_tasks_run_id",
            "t_test_runs",
            ["run_id"],
            ["id"],
            ondelete="CASCADE",
        )

    _backfill_task_run_ids()

    op.create_index("ix_t_durable_tasks_run_id", "t_durable_tasks", ["run_id"])
    op.create_index(
        "ix_t_durable_tasks_task_status_run",
        "t_durable_tasks",
        ["task_type", "status", "run_id"],
    )
    op.create_index(
        "ix_t_log_records_log_type_created",
        "t_log_records",
        ["log_type", "created_at"],
    )
    op.create_index(
        "ix_t_log_records_database_scope_created",
        "t_log_records",
        ["database_scope", "created_at"],
    )
    op.create_index(
        "ix_t_log_records_sql_fingerprint_created",
        "t_log_records",
        ["sql_fingerprint", "created_at"],
    )
    op.create_index(
        "ix_t_log_records_result_created",
        "t_log_records",
        ["result", "created_at"],
    )
    op.create_index(
        "ix_t_audit_logs_action_created",
        "t_audit_logs",
        ["action", "created_at"],
    )
    op.create_index(
        "ix_t_audit_logs_object_type_created",
        "t_audit_logs",
        ["object_type", "created_at"],
    )
    op.create_index(
        "ix_t_resource_locks_active_resource",
        "t_resource_locks",
        ["resource_id", "released_at"],
    )


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
