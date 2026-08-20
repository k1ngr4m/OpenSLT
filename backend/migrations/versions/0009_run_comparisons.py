"""Add immutable run comparison snapshots.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa

from app.core.types import JSONText


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _mysql_options() -> dict:
    if op.get_context().dialect.name != "mysql":
        return {}
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
    }


def upgrade() -> None:
    op.create_table(
        "t_run_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("baseline_run_id", sa.Integer(), nullable=True),
        sa.Column("target_run_number", sa.String(length=40), nullable=False),
        sa.Column("baseline_run_number", sa.String(length=40), nullable=False),
        sa.Column("target_metrics_checksum", sa.String(length=64), nullable=False),
        sa.Column("baseline_metrics_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_metrics_snapshot", JSONText(), nullable=False),
        sa.Column("baseline_metrics_snapshot", JSONText(), nullable=False),
        sa.Column("target_analysis_refs", JSONText(), nullable=False),
        sa.Column("baseline_analysis_refs", JSONText(), nullable=False),
        sa.Column("comparison_rows", JSONText(), nullable=False),
        sa.Column("warnings", JSONText(), nullable=False),
        sa.Column("is_compatible", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["baseline_run_id"], ["t_test_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["t_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["t_test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_run_comparison_run"),
        **_mysql_options(),
    )
    op.create_index("ix_t_run_comparisons_run_id", "t_run_comparisons", ["run_id"], unique=False)
    op.create_index("ix_run_comparison_baseline", "t_run_comparisons", ["baseline_run_id"], unique=False)
    op.create_index("ix_t_run_comparisons_created_by", "t_run_comparisons", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_t_run_comparisons_created_by", table_name="t_run_comparisons")
    op.drop_index("ix_run_comparison_baseline", table_name="t_run_comparisons")
    op.drop_index("ix_t_run_comparisons_run_id", table_name="t_run_comparisons")
    op.drop_table("t_run_comparisons")
