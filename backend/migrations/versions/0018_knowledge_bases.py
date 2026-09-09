"""Isolate knowledge bases and bind existing knowledge consumers."""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _mysql_options():
    return dict(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci") if op.get_context().dialect.name == "mysql" else {}


def upgrade():
    # MySQL DDL survives a failed migration even when the revision stamp does not.
    inspector = None if op.get_context().as_sql else sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names()) if inspector else set()
    if "t_knowledge_bases" not in existing_tables:
        op.create_table("t_knowledge_bases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("embedding_model_id", sa.Integer(), sa.ForeignKey("t_ai_models.id", ondelete="RESTRICT")),
            sa.Column("chunk_size", sa.Integer(), nullable=False),
            sa.Column("chunk_overlap", sa.Integer(), nullable=False),
            sa.Column("top_k", sa.Integer(), nullable=False),
            sa.Column("index_status", sa.String(24), nullable=False),
            sa.Column("last_success_at", sa.DateTime()),
            sa.Column("last_error", sa.Text()),
            sa.Column("legacy_index", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False), **_mysql_options())
    if "t_knowledge_uploads" not in existing_tables:
        op.create_table("t_knowledge_uploads",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("knowledge_base_id", sa.Integer(), sa.ForeignKey("t_knowledge_bases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("storage_name", sa.String(64), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("pending_delete", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("knowledge_base_id", "name", name="uq_knowledge_upload_name"), **_mysql_options())
    upload_indexes = (
        {index["name"] for index in inspector.get_indexes("t_knowledge_uploads")}
        if "t_knowledge_uploads" in existing_tables else set()
    )
    if "ix_t_knowledge_uploads_knowledge_base_id" not in upload_indexes:
        op.create_index("ix_t_knowledge_uploads_knowledge_base_id", "t_knowledge_uploads", ["knowledge_base_id"])
    for table in ("t_svn_knowledge_sources", "t_smart_case_generations", "t_chat_conversations"):
        columns = {column["name"] for column in inspector.get_columns(table)} if inspector else set()
        foreign_keys = inspector.get_foreign_keys(table) if inspector else []
        indexes = {index["name"] for index in inspector.get_indexes(table)} if inspector else set()
        missing_foreign_key = not any(
            item["constrained_columns"] == ["knowledge_base_id"]
            and item["referred_table"] == "t_knowledge_bases"
            for item in foreign_keys
        )
        index_name = "ix_" + table + "_knowledge_base_id"
        if "knowledge_base_id" not in columns or missing_foreign_key or index_name not in indexes:
            with op.batch_alter_table(table) as batch:
                if "knowledge_base_id" not in columns:
                    batch.add_column(sa.Column("knowledge_base_id", sa.Integer(), nullable=True))
                if missing_foreign_key:
                    batch.create_foreign_key("fk_" + table + "_knowledge_base", "t_knowledge_bases", ["knowledge_base_id"], ["id"], ondelete="CASCADE" if table == "t_svn_knowledge_sources" else "RESTRICT")
                if index_name not in indexes:
                    batch.create_index(index_name, ["knowledge_base_id"], unique=table == "t_svn_knowledge_sources")
    op.execute(sa.text("""INSERT INTO t_knowledge_bases
        (id, name, description, embedding_model_id, chunk_size, chunk_overlap, top_k, index_status, legacy_index, created_at, updated_at)
        SELECT source.id, '默认知识库', '', (SELECT model_id FROM t_active_ai_models WHERE kind = 'embedding'),
               1200, 150, 10, 'stale', CASE WHEN source.id = (SELECT MIN(id) FROM t_svn_knowledge_sources) THEN 1 ELSE 0 END, source.created_at, source.updated_at
        FROM t_svn_knowledge_sources AS source
        LEFT JOIN t_knowledge_bases AS existing ON existing.id = source.id
        WHERE source.knowledge_base_id IS NULL AND existing.id IS NULL"""))
    op.execute(sa.text("UPDATE t_svn_knowledge_sources SET knowledge_base_id = id WHERE knowledge_base_id IS NULL"))
    for table, condition in (("t_smart_case_generations", "1=1"), ("t_chat_conversations", "mode = 'knowledge'")):
        op.execute(sa.text("UPDATE " + table + " SET knowledge_base_id = (SELECT MIN(knowledge_base_id) FROM t_svn_knowledge_sources) WHERE knowledge_base_id IS NULL AND " + condition))


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM t_knowledge_bases")).scalar() > 1 or connection.execute(sa.text("SELECT COUNT(*) FROM t_knowledge_uploads")).scalar():
        raise RuntimeError("多知识库或上传文档不能降级为单知识源，请先备份并处理数据")
    for table in ("t_chat_conversations", "t_smart_case_generations", "t_svn_knowledge_sources"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index("ix_" + table + "_knowledge_base_id")
            batch.drop_constraint("fk_" + table + "_knowledge_base", type_="foreignkey")
            batch.drop_column("knowledge_base_id")
    op.drop_table("t_knowledge_uploads")
    op.drop_table("t_knowledge_bases")
