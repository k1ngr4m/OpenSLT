"""Store parsed attachments with private chat messages."""
from alembic import op
import sqlalchemy as sa
from app.core.types import JSONText

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    if not op.get_context().as_sql:
        columns = sa.inspect(op.get_bind()).get_columns("t_chat_messages")
        if any(column["name"] == "attachments" for column in columns):
            return
    # Nullable addition works on legacy MySQL without a TEXT default; backfill old rows.
    op.add_column("t_chat_messages", sa.Column("attachments", JSONText(), nullable=True))
    op.execute("UPDATE t_chat_messages SET attachments = '[]' WHERE attachments IS NULL")


def downgrade():
    with op.batch_alter_table("t_chat_messages") as batch:
        batch.drop_column("attachments")
