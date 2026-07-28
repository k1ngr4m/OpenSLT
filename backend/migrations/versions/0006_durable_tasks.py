"""Add durable background tasks.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _create_index_if_missing(
    existing_indexes: set,
    name: str,
    columns: list,
    *,
    unique: bool = False,
    **dialect_options: object,
) -> None:
    if name in existing_indexes:
        return
    op.create_index(
        name,
        "t_durable_tasks",
        columns,
        unique=unique,
        **dialect_options,
    )
    existing_indexes.add(name)


def upgrade() -> None:
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    table_exists = bool(inspector and inspector.has_table("t_durable_tasks"))

    if not table_exists:
        op.create_table(
            "t_durable_tasks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("task_type", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(length=128), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = (
        {item["name"] for item in inspector.get_indexes("t_durable_tasks")}
        if table_exists and inspector
        else set()
    )
    _create_index_if_missing(existing_indexes, "ix_t_durable_tasks_task_type", ["task_type"])
    _create_index_if_missing(
        existing_indexes,
        "ix_t_durable_tasks_idempotency_key",
        ["idempotency_key"],
        unique=True,
        mysql_length=191,
    )
    _create_index_if_missing(existing_indexes, "ix_t_durable_tasks_status", ["status"])
    _create_index_if_missing(existing_indexes, "ix_t_durable_tasks_available_at", ["available_at"])
    _create_index_if_missing(
        existing_indexes,
        "ix_t_durable_tasks_lease_expires_at",
        ["lease_expires_at"],
    )
    _create_index_if_missing(existing_indexes, "ix_t_durable_tasks_locked_by", ["locked_by"])
    _create_index_if_missing(
        existing_indexes,
        "ix_durable_task_dispatch",
        ["status", "available_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_durable_task_dispatch", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_locked_by", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_lease_expires_at", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_available_at", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_status", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_idempotency_key", table_name="t_durable_tasks")
    op.drop_index("ix_t_durable_tasks_task_type", table_name="t_durable_tasks")
    op.drop_table("t_durable_tasks")
