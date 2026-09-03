"""Add smart-case generation and multiple SVN repository URLs.

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa

from app.core.types import JSONText


revision = "0011"
down_revision = "0010"
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
    context = op.get_context()
    inspector = None if getattr(context, "as_sql", False) else sa.inspect(op.get_bind())
    source_columns = (
        {column["name"]: column for column in inspector.get_columns("t_svn_knowledge_sources")}
        if inspector
        else {}
    )
    columns = (
        sa.Column("llm_base_url", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("llm_model", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("encrypted_llm_api_key", sa.Text(), nullable=True),
        sa.Column(
            "allow_insecure_llm_http",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("repository_urls", JSONText(), nullable=True),
    )
    for column in columns:
        if not inspector or column.name not in source_columns:
            op.add_column("t_svn_knowledge_sources", column)
    op.execute(sa.text("UPDATE t_svn_knowledge_sources SET repository_urls = '[]' WHERE repository_urls IS NULL"))
    repository_urls_nullable = (
        not inspector
        or "repository_urls" not in source_columns
        or source_columns["repository_urls"]["nullable"]
    )
    if repository_urls_nullable:
        with op.batch_alter_table("t_svn_knowledge_sources") as batch_op:
            batch_op.alter_column("repository_urls", existing_type=JSONText(), nullable=False)

    generation_table_exists = inspector is not None and inspector.has_table("t_smart_case_generations")
    if not generation_table_exists:
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
    existing_indexes = (
        {index["name"] for index in inspector.get_indexes("t_smart_case_generations")}
        if generation_table_exists
        else set()
    )
    indexes = (
        ("ix_t_smart_case_generations_requirement_path", ["requirement_path"]),
        ("ix_t_smart_case_generations_requirement_no", ["requirement_no"]),
        ("ix_t_smart_case_generations_status", ["status"]),
        ("ix_t_smart_case_generations_created_by", ["created_by"]),
    )
    for name, index_columns in indexes:
        if name not in existing_indexes:
            op.create_index(name, "t_smart_case_generations", index_columns)


def downgrade() -> None:
    op.drop_index("ix_t_smart_case_generations_created_by", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_status", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_requirement_no", table_name="t_smart_case_generations")
    op.drop_index("ix_t_smart_case_generations_requirement_path", table_name="t_smart_case_generations")
    op.drop_table("t_smart_case_generations")
    op.drop_column("t_svn_knowledge_sources", "repository_urls")
    op.drop_column("t_svn_knowledge_sources", "allow_insecure_llm_http")
    op.drop_column("t_svn_knowledge_sources", "encrypted_llm_api_key")
    op.drop_column("t_svn_knowledge_sources", "llm_model")
    op.drop_column("t_svn_knowledge_sources", "llm_base_url")
