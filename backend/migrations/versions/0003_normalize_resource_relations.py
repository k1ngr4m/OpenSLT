"""Normalize scenario, workflow-version, and run resource relations.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import json
import typing

import sqlalchemy as sa
from alembic import context, op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


RELATIONS = (
    (
        "t_scenario_resources",
        "scenario_id",
        "t_test_scenarios",
        "default_resource_ids",
        "uq_scenario_resource",
        "uq_scenario_resource_position",
    ),
    (
        "t_workflow_version_resources",
        "workflow_version_id",
        "t_scenario_workflow_versions",
        "resource_ids",
        "uq_workflow_version_resource",
        "uq_workflow_version_resource_position",
    ),
    (
        "t_run_resources",
        "run_id",
        "t_test_runs",
        "resource_ids",
        "uq_run_resource",
        "uq_run_resource_position",
    ),
)


def _resource_ids(value: typing.Any) -> typing.List[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            resource_id = int(item)
        except (TypeError, ValueError):
            continue
        if resource_id > 0 and resource_id not in seen:
            result.append(resource_id)
            seen.add(resource_id)
    return result


def _create_relation_table(
    table_name: str,
    owner_column: str,
    owner_table: str,
    resource_unique: str,
    position_unique: str,
) -> None:
    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(owner_column, sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint([owner_column], [f"{owner_table}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["t_resources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(owner_column, "resource_id", name=resource_unique),
        sa.UniqueConstraint(owner_column, "position", name=position_unique),
    )
    op.create_index(f"ix_{table_name}_{owner_column}", table_name, [owner_column])
    op.create_index(f"ix_{table_name}_resource_id", table_name, ["resource_id"])


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute("-- Resource relation backfill runs during online migration")
        return
    connection = op.get_bind()
    for table_name, owner_column, owner_table, json_column, _, _ in RELATIONS:
        rows = connection.execute(
            sa.text(f"SELECT id, {json_column} FROM {owner_table}")
        ).mappings()
        values = []
        for row in rows:
            for position, resource_id in enumerate(_resource_ids(row[json_column]), 1):
                values.append(
                    {
                        owner_column: row["id"],
                        "resource_id": resource_id,
                        "position": position,
                    }
                )
        if values:
            relation = sa.table(
                table_name,
                sa.column(owner_column, sa.Integer()),
                sa.column("resource_id", sa.Integer()),
                sa.column("position", sa.Integer()),
            )
            op.bulk_insert(relation, values)


def upgrade() -> None:
    for table_name, owner_column, owner_table, _, resource_unique, position_unique in RELATIONS:
        _create_relation_table(
            table_name,
            owner_column,
            owner_table,
            resource_unique,
            position_unique,
        )
    _backfill()


def downgrade() -> None:
    for table_name, owner_column, *_ in reversed(RELATIONS):
        op.drop_index(f"ix_{table_name}_resource_id", table_name=table_name)
        op.drop_index(f"ix_{table_name}_{owner_column}", table_name=table_name)
        op.drop_table(table_name)
