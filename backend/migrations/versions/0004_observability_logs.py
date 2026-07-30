"""Add searchable HTTP and SQL observability fields.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("t_log_records") as batch:
        batch.add_column(sa.Column("event_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("result", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("http_method", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("database_scope", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("sql_fingerprint", sa.String(length=64), nullable=True))
        batch.create_index("ix_t_log_records_event_id", ["event_id"], unique=True)
        batch.create_index("ix_t_log_records_duration_ms", ["duration_ms"])
        batch.create_index("ix_t_log_records_result", ["result"])
        batch.create_index("ix_t_log_records_http_method", ["http_method"])
        batch.create_index("ix_t_log_records_http_status", ["http_status"])
        batch.create_index("ix_t_log_records_database_scope", ["database_scope"])
        batch.create_index("ix_t_log_records_sql_fingerprint", ["sql_fingerprint"])


def downgrade() -> None:
    with op.batch_alter_table("t_log_records") as batch:
        batch.drop_index("ix_t_log_records_sql_fingerprint")
        batch.drop_index("ix_t_log_records_database_scope")
        batch.drop_index("ix_t_log_records_http_status")
        batch.drop_index("ix_t_log_records_http_method")
        batch.drop_index("ix_t_log_records_result")
        batch.drop_index("ix_t_log_records_duration_ms")
        batch.drop_index("ix_t_log_records_event_id")
        batch.drop_column("sql_fingerprint")
        batch.drop_column("database_scope")
        batch.drop_column("http_status")
        batch.drop_column("http_method")
        batch.drop_column("result")
        batch.drop_column("duration_ms")
        batch.drop_column("event_id")
