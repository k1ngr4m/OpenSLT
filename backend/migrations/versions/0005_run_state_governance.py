"""Add optimistic run status versions and transition history.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "t_test_runs",
        sa.Column("status_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "t_run_status_transitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=False),
        sa.Column("to_status", sa.String(length=40), nullable=False),
        sa.Column("status_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="service"),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["t_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["t_test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "status_version", name="uq_run_status_transition_version"
        ),
    )
    op.create_index(
        "ix_t_run_status_transitions_run_id",
        "t_run_status_transitions",
        ["run_id"],
    )
    op.create_index(
        "ix_t_run_status_transitions_source",
        "t_run_status_transitions",
        ["source"],
    )
    op.create_index(
        "ix_t_run_status_transitions_actor_id",
        "t_run_status_transitions",
        ["actor_id"],
    )
    op.create_index(
        "ix_t_run_status_transitions_created_at",
        "t_run_status_transitions",
        ["created_at"],
    )
    op.create_index(
        "ix_run_status_transition_created",
        "t_run_status_transitions",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_status_transition_created", table_name="t_run_status_transitions")
    op.drop_index("ix_t_run_status_transitions_created_at", table_name="t_run_status_transitions")
    op.drop_index("ix_t_run_status_transitions_actor_id", table_name="t_run_status_transitions")
    op.drop_index("ix_t_run_status_transitions_source", table_name="t_run_status_transitions")
    op.drop_index("ix_t_run_status_transitions_run_id", table_name="t_run_status_transitions")
    op.drop_table("t_run_status_transitions")
    op.drop_column("t_test_runs", "status_version")
