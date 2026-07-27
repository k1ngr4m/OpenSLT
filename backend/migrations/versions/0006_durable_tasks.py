"""Add durable background tasks.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index("ix_t_durable_tasks_task_type", "t_durable_tasks", ["task_type"])
    op.create_index("ix_t_durable_tasks_idempotency_key", "t_durable_tasks", ["idempotency_key"], unique=True)
    op.create_index("ix_t_durable_tasks_status", "t_durable_tasks", ["status"])
    op.create_index("ix_t_durable_tasks_available_at", "t_durable_tasks", ["available_at"])
    op.create_index("ix_t_durable_tasks_lease_expires_at", "t_durable_tasks", ["lease_expires_at"])
    op.create_index("ix_t_durable_tasks_locked_by", "t_durable_tasks", ["locked_by"])
    op.create_index(
        "ix_durable_task_dispatch",
        "t_durable_tasks",
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
