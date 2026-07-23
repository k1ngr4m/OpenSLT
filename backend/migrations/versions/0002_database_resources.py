"""Add MySQL database resources and update confirmations.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("database_engine", sa.String(length=32), nullable=True))
    op.add_column("resources", sa.Column("database_connection_mode", sa.String(length=32), nullable=True))
    op.add_column("resources", sa.Column("database_host", sa.String(length=255), nullable=True))
    op.add_column("resources", sa.Column("database_port", sa.Integer(), nullable=True))
    op.add_column("resources", sa.Column("database_names", sa.JSON(), nullable=True))
    op.add_column("resources", sa.Column("database_username", sa.String(length=128), nullable=True))
    op.add_column("resources", sa.Column("encrypted_database_password", sa.Text(), nullable=True))
    op.add_column(
        "resources",
        sa.Column("database_tls_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "database_update_confirmations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("sql_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("estimated_rows", sa.Integer(), nullable=False),
        sa.Column("actual_rows", sa.Integer(), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("actor_id", "resource_id", "sql_fingerprint", "status", "expires_at"):
        op.create_index(
            f"ix_database_update_confirmations_{column}",
            "database_update_confirmations",
            [column],
        )


def downgrade() -> None:
    op.drop_table("database_update_confirmations")
    for column in (
        "database_tls_enabled",
        "encrypted_database_password",
        "database_username",
        "database_names",
        "database_port",
        "database_host",
        "database_connection_mode",
        "database_engine",
    ):
        op.drop_column("resources", column)
