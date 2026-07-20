"""Initial OpenSLT schema.

Revision ID: 0001
"""
from alembic import op

from app.core.database import Base
from app.models import *  # noqa: F403

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())

