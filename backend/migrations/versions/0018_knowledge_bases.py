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
    op.create_index("ix_t_knowledge_uploads_knowledge_base_id", "t_knowledge_uploads", ["knowledge_base_id"])
    for table in ("t_svn_knowledge_sources", "t_smart_case_generations", "t_chat_conversations"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("knowledge_base_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_" + table + "_knowledge_base", "t_knowledge_bases", ["knowledge_base_id"], ["id"], ondelete="CASCADE" if table == "t_svn_knowledge_sources" else "RESTRICT")
            batch.create_index("ix_" + table + "_knowledge_base_id", ["knowledge_base_id"], unique=table == "t_svn_knowledge_sources")
    op.execute(sa.text("""INSERT INTO t_knowledge_bases
        (id, name, description, embedding_model_id, chunk_size, chunk_overlap, top_k, index_status, legacy_index, created_at, updated_at)
        SELECT id, '默认知识库', '', (SELECT model_id FROM t_active_ai_models WHERE kind = 'embedding'),
               1200, 150, 10, 'stale', CASE WHEN id = (SELECT MIN(id) FROM t_svn_knowledge_sources) THEN 1 ELSE 0 END, created_at, updated_at
        FROM t_svn_knowledge_sources"""))
    op.execute(sa.text("UPDATE t_svn_knowledge_sources SET knowledge_base_id = id"))
    for table, condition in (("t_smart_case_generations", "1=1"), ("t_chat_conversations", "mode = 'knowledge'")):
        op.execute(sa.text("UPDATE " + table + " SET knowledge_base_id = (SELECT MIN(id) FROM t_knowledge_bases) WHERE " + condition))


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
