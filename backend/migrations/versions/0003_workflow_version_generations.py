"""Allow private generations within a workflow version.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("t_scenario_workflow_versions") as batch:
        batch.add_column(
            sa.Column("generation_no", sa.Integer(), nullable=False, server_default="1")
        )
        batch.drop_constraint("uq_scenario_workflow_version", type_="unique")
        batch.create_unique_constraint(
            "uq_scenario_workflow_version_generation",
            ["scenario_id", "version_no", "generation_no"],
        )


def downgrade() -> None:
    with op.batch_alter_table("t_scenario_workflow_versions") as batch:
        batch.drop_constraint(
            "uq_scenario_workflow_version_generation", type_="unique"
        )
        batch.create_unique_constraint(
            "uq_scenario_workflow_version", ["scenario_id", "version_no"]
        )
        batch.drop_column("generation_no")
