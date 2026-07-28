"""Replace REM wiring profiles with trade and query configuration.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("t_resources", sa.Column("trade_ip", sa.String(length=45), nullable=True))
    op.add_column("t_resources", sa.Column("trade_tcp_port", sa.Integer(), nullable=True))
    op.add_column("t_resources", sa.Column("trade_udp_port", sa.Integer(), nullable=True))
    op.add_column("t_resources", sa.Column("query_ip", sa.String(length=45), nullable=True))
    op.add_column("t_resources", sa.Column("query_port", sa.Integer(), nullable=True))
    op.drop_column("t_resources", "wiring_profile")


def downgrade() -> None:
    op.add_column("t_resources", sa.Column("wiring_profile", sa.Text(), nullable=True))
    op.drop_column("t_resources", "query_port")
    op.drop_column("t_resources", "query_ip")
    op.drop_column("t_resources", "trade_udp_port")
    op.drop_column("t_resources", "trade_tcp_port")
    op.drop_column("t_resources", "trade_ip")
