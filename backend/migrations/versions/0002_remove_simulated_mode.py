"""Remove simulated-mode database update flag.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("t_database_update_confirmations") as batch_op:
        batch_op.drop_column("simulated")


def downgrade() -> None:
    with op.batch_alter_table("t_database_update_confirmations") as batch_op:
        batch_op.add_column(sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("t_database_update_confirmations") as batch_op:
        batch_op.alter_column("simulated", server_default=None)
