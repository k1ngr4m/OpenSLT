"""Add private database configuration templates.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa

from app.core.types import JSONText


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    options = {}
    if op.get_context().dialect.name != "sqlite":
        options = {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        }
    op.create_table(
        "t_database_config_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("keys", JSONText(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["t_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id",
            "normalized_name",
            name="uq_database_config_template_user_name",
        ),
        **options,
    )
    op.create_index(
        "ix_t_database_config_templates_user_id",
        "t_database_config_templates",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_table("t_database_config_templates")
