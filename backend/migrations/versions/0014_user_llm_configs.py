"""Personal LLM settings and immutable generation connection snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "t_user_llm_configs",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("t_users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("base_url", sa.String(1024), nullable=False),
        sa.Column("model_id", sa.String(160), nullable=False),
        sa.Column("encrypted_api_key", sa.Text()),
        sa.Column("allow_insecure_http", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
    )
    op.add_column("t_smart_case_generations", sa.Column("encrypted_llm_config", sa.Text()))


def downgrade() -> None:
    op.drop_column("t_smart_case_generations", "encrypted_llm_config")
    op.drop_table("t_user_llm_configs")
