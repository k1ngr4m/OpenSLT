"""Private chat conversations and cited messages.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from app.core.types import JSONText

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    options = dict(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
    op.create_table(
        "t_chat_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("t_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        **options,
    )
    op.create_index("ix_t_chat_conversations_user_id", "t_chat_conversations", ["user_id"])
    op.create_table(
        "t_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("t_chat_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("sources", JSONText(), nullable=False),
        sa.Column("error", sa.String(512)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        **options,
    )
    op.create_index("ix_t_chat_messages_conversation_id", "t_chat_messages", ["conversation_id"])
    op.create_index("ix_t_chat_messages_status", "t_chat_messages", ["status"])


def downgrade() -> None:
    op.drop_table("t_chat_messages")
    op.drop_table("t_chat_conversations")
