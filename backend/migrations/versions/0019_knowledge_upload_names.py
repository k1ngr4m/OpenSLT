"""Remove the upload-name index unsupported by legacy InnoDB."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    # Corrected 0018 omits this index. Only databases upgraded with the old
    # revision need it removed; introspection also makes interrupted DDL resumable.
    if op.get_context().as_sql:
        return
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("t_knowledge_uploads")
    if any(item["name"] == "uq_knowledge_upload_name" for item in constraints):
        with op.batch_alter_table("t_knowledge_uploads") as batch:
            batch.drop_constraint("uq_knowledge_upload_name", type_="unique")


def downgrade():
    # Corrected 0018 uses the same schema. Do not restore the incompatible index.
    pass
