"""Bind concrete resources to scenarios and remove JSON rules.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

from app.core.types import JSONText

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _clean_run_snapshots() -> None:
    connection = op.get_bind()
    runs = sa.table(
        "test_runs",
        sa.column("id", sa.Integer),
        sa.column("config_snapshot", JSONText()),
    )
    rows = connection.execute(sa.select(runs.c.id, runs.c.config_snapshot)).mappings().all()
    for row in rows:
        snapshot = dict(row["config_snapshot"] or {})
        scenario = dict(snapshot.get("scenario") or {})
        changed = False
        for key in ("parameters", "actions", "statistics_rules"):
            if key in scenario:
                scenario.pop(key)
                changed = True
        if changed:
            snapshot["scenario"] = scenario
            connection.execute(
                sa.update(runs).where(runs.c.id == row["id"]).values(config_snapshot=snapshot)
            )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    scenario_columns = {column["name"]: column for column in inspector.get_columns("test_scenarios")}
    added_default_resources = "default_resource_ids" not in scenario_columns
    if added_default_resources:
        op.add_column("test_scenarios", sa.Column("default_resource_ids", JSONText(), nullable=True))
        scenarios = sa.table(
            "test_scenarios",
            sa.column("default_resource_ids", JSONText()),
        )
        op.get_bind().execute(sa.update(scenarios).values(default_resource_ids=[]))
    _clean_run_snapshots()

    with op.batch_alter_table("test_scenarios") as batch_op:
        if added_default_resources or scenario_columns["default_resource_ids"]["nullable"]:
            batch_op.alter_column("default_resource_ids", existing_type=JSONText(), nullable=False)
        for column in ("parameters", "actions", "statistics_rules"):
            if column in scenario_columns:
                batch_op.drop_column(column)
    metric_columns = {column["name"] for column in inspector.get_columns("metrics")}
    if "rule_result" in metric_columns:
        with op.batch_alter_table("metrics") as batch_op:
            batch_op.drop_column("rule_result")
    verdict_columns = {column["name"] for column in inspector.get_columns("verdicts")}
    if "automatic_result" in verdict_columns:
        with op.batch_alter_table("verdicts") as batch_op:
            batch_op.drop_column("automatic_result")


def downgrade() -> None:
    op.add_column("test_scenarios", sa.Column("parameters", JSONText(), nullable=True))
    op.add_column("test_scenarios", sa.Column("actions", JSONText(), nullable=True))
    op.add_column("test_scenarios", sa.Column("statistics_rules", JSONText(), nullable=True))
    op.add_column("metrics", sa.Column("rule_result", sa.String(length=32), nullable=True))
    op.add_column("verdicts", sa.Column("automatic_result", sa.String(length=32), nullable=True))

    scenarios = sa.table(
        "test_scenarios",
        sa.column("parameters", JSONText()),
        sa.column("actions", JSONText()),
        sa.column("statistics_rules", JSONText()),
    )
    verdicts = sa.table("verdicts", sa.column("automatic_result", sa.String(length=32)))
    connection = op.get_bind()
    connection.execute(sa.update(scenarios).values(parameters={}, actions=[], statistics_rules={}))
    connection.execute(sa.update(verdicts).values(automatic_result="pending"))

    with op.batch_alter_table("test_scenarios") as batch_op:
        batch_op.alter_column("parameters", existing_type=JSONText(), nullable=False)
        batch_op.alter_column("actions", existing_type=JSONText(), nullable=False)
        batch_op.alter_column("statistics_rules", existing_type=JSONText(), nullable=False)
        batch_op.drop_column("default_resource_ids")
    with op.batch_alter_table("verdicts") as batch_op:
        batch_op.alter_column("automatic_result", existing_type=sa.String(length=32), nullable=False)
