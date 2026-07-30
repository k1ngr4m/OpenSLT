"""Store descriptions on configuration capture items.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("t_configuration_capture_items") as batch:
        batch.add_column(sa.Column("item_description", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("t_configuration_capture_items") as batch:
        batch.drop_column("item_description")
