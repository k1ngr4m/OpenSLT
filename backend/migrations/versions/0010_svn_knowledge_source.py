"""Add the singleton SVN knowledge source.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa

from app.core.types import JSONText


revision = "0010"
down_revision = "0009"
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
    op.create_table(
        "t_svn_knowledge_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_url", sa.String(length=1024), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("embedding_base_url", sa.String(length=1024), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("encrypted_embedding_api_key", sa.Text(), nullable=True),
        sa.Column("allow_insecure_embedding_http", sa.Boolean(), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("include_paths", JSONText(), nullable=False),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allow_insecure_http", sa.Boolean(), nullable=False),
        sa.Column("sync_status", sa.String(length=24), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_revisions", JSONText(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("failed_file_count", sa.Integer(), nullable=False),
        sa.Column("last_changes", JSONText(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        **_mysql_options(),
    )


def downgrade() -> None:
    op.drop_table("t_svn_knowledge_sources")
