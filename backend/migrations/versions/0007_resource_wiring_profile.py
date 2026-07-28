"""Add REM wiring profiles.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_resources", sa.Column("wiring_profile", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("t_resources", "wiring_profile")
