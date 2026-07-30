"""Add directories above test plans.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _mysql_options() -> dict:
    if op.get_context().dialect.name != "mysql":
        return {}
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
    }


def upgrade() -> None:
    directory_table = op.create_table(
        "t_plan_directories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        **_mysql_options(),
    )
    op.create_index(
        "ix_t_plan_directories_is_default",
        "t_plan_directories",
        ["is_default"],
        unique=False,
    )
    now = datetime.utcnow()
    op.bulk_insert(
        directory_table,
        [{"id": 1, "name": "默认目录", "is_default": True, "created_at": now, "updated_at": now}],
    )

    with op.batch_alter_table("t_test_plans") as batch:
        batch.add_column(sa.Column("directory_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE t_test_plans SET directory_id = 1"))
    with op.batch_alter_table("t_test_plans") as batch:
        batch.alter_column("directory_id", existing_type=sa.Integer(), nullable=False)
        batch.create_index("ix_t_test_plans_directory_id", ["directory_id"], unique=False)
        batch.create_foreign_key(
            "fk_test_plans_directory_id",
            "t_plan_directories",
            ["directory_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("t_test_plans") as batch:
        batch.drop_constraint("fk_test_plans_directory_id", type_="foreignkey")
        batch.drop_index("ix_t_test_plans_directory_id")
        batch.drop_column("directory_id")
    op.drop_index("ix_t_plan_directories_is_default", table_name="t_plan_directories")
    op.drop_table("t_plan_directories")
