"""Configure and validate Embedding provider dimensions."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_model_providers", sa.Column("embedding_dimensions", sa.Integer(), nullable=False, server_default="1024"))
    # Keep the working dimensions of the currently published knowledge index.
    op.execute(sa.text("""
        UPDATE t_model_providers SET embedding_dimensions = (
            SELECT embedding_dimensions FROM t_svn_knowledge_sources
            WHERE embedding_dimensions > 0 ORDER BY id LIMIT 1
        ) WHERE user_id IS NULL AND id IN (
            SELECT provider_id FROM t_ai_models WHERE id IN (
                SELECT model_id FROM t_active_ai_models WHERE kind = 'embedding'
            )
        ) AND EXISTS (SELECT 1 FROM t_svn_knowledge_sources WHERE embedding_dimensions > 0)
    """))


def downgrade() -> None:
    with op.batch_alter_table("t_model_providers") as batch:
        batch.drop_column("embedding_dimensions")
