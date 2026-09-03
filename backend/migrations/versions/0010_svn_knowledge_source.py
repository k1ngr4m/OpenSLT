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
        sa.Column("llm_base_url", sa.String(length=1024), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("encrypted_llm_api_key", sa.Text(), nullable=True),
        sa.Column("allow_insecure_llm_http", sa.Boolean(), nullable=False),
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
    op.create_table(
        "t_smart_case_generations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requirement_path", sa.String(length=1024), nullable=False),
        sa.Column("requirement_revision", sa.String(length=64), nullable=False),
        sa.Column("requirement_no", sa.String(length=64), nullable=True),
        sa.Column("requirement_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("index_revisions", JSONText(), nullable=False),
        sa.Column("referenced_sources", JSONText(), nullable=False),
        sa.Column("result_cases", JSONText(), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("artifact_path", sa.String(length=1024), nullable=True),
        sa.Column("artifact_size", sa.Integer(), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["t_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        **_mysql_options(),
    )
    op.create_index("ix_t_smart_case_generations_requirement_path", "t_smart_case_generations", ["requirement_path"], unique=False)
    op.create_index("ix_t_smart_case_generations_requirement_no", "t_smart_case_generations", ["requirement_no"], unique=False)
    op.create_index("ix_t_smart_case_generations_status", "t_smart_case_generations", ["status"], unique=False)
    op.create_index("ix_t_smart_case_generations_created_by", "t_smart_case_generations", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_t_smart_case_generations_created_by", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_status", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_requirement_no", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_requirement_path", table_name="t_smart_case_generations")
    op.drop_table("t_smart_case_generations")
    op.drop_table("t_svn_knowledge_sources")
