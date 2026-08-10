"""Add a nullable unique idempotency key to artifacts.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("t_artifacts") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(191), nullable=True))
        batch.create_unique_constraint(
            "uq_t_artifacts_idempotency_key",
            ["idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("t_artifacts") as batch:
        batch.drop_constraint("uq_t_artifacts_idempotency_key", type_="unique")
        batch.drop_column("idempotency_key")
