"""Normalize plan resources and workflow-node contract files.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import json
import typing

import sqlalchemy as sa
from alembic import context, op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _json_value(value: typing.Any, default: typing.Any) -> typing.Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _ids(value: typing.Any) -> typing.List[int]:
    value = _json_value(value, [])
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in seen:
            result.append(item_id)
            seen.add(item_id)
    return result


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute("-- Plan and contract relation backfill runs during online migration")
        return

    connection = op.get_bind()
    resource_ids = set(connection.execute(sa.text("SELECT id FROM t_resources")).scalars())
    plan_values = []
    for row in connection.execute(
        sa.text("SELECT id, default_resource_ids FROM t_test_plans")
    ).mappings():
        for position, resource_id in enumerate(_ids(row["default_resource_ids"]), 1):
            if resource_id in resource_ids:
                plan_values.append(
                    {"plan_id": row["id"], "resource_id": resource_id, "position": position}
                )
    if plan_values:
        op.bulk_insert(
            sa.table(
                "t_plan_resources",
                sa.column("plan_id", sa.Integer()),
                sa.column("resource_id", sa.Integer()),
                sa.column("position", sa.Integer()),
            ),
            plan_values,
        )

    contract_ids = set(
        connection.execute(sa.text("SELECT id FROM t_contract_data_files")).scalars()
    )
    contract_values = []
    for row in connection.execute(
        sa.text("SELECT id, config FROM t_scenario_workflow_nodes")
    ).mappings():
        config = _json_value(row["config"], {})
        legacy_ids = config.get("contract_file_ids", []) if isinstance(config, dict) else []
        position = 0
        for contract_file_id in _ids(legacy_ids):
            if contract_file_id not in contract_ids:
                continue
            position += 1
            contract_values.append(
                {
                    "workflow_node_id": row["id"],
                    "contract_file_id": contract_file_id,
                    "position": position,
                }
            )
    if contract_values:
        op.bulk_insert(
            sa.table(
                "t_workflow_node_contract_files",
                sa.column("workflow_node_id", sa.Integer()),
                sa.column("contract_file_id", sa.Integer()),
                sa.column("position", sa.Integer()),
            ),
            contract_values,
        )


def upgrade() -> None:
    op.create_table(
        "t_plan_resources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["t_test_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["t_resources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "resource_id", name="uq_plan_resource"),
        sa.UniqueConstraint("plan_id", "position", name="uq_plan_resource_position"),
    )
    op.create_index("ix_t_plan_resources_plan_id", "t_plan_resources", ["plan_id"])
    op.create_index("ix_t_plan_resources_resource_id", "t_plan_resources", ["resource_id"])

    op.create_table(
        "t_workflow_node_contract_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_node_id", sa.Integer(), nullable=False),
        sa.Column("contract_file_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_node_id"], ["t_scenario_workflow_nodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["contract_file_id"], ["t_contract_data_files.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_node_id", "contract_file_id", name="uq_workflow_node_contract_file"
        ),
        sa.UniqueConstraint(
            "workflow_node_id", "position", name="uq_workflow_node_contract_position"
        ),
    )
    op.create_index(
        "ix_t_workflow_node_contract_files_workflow_node_id",
        "t_workflow_node_contract_files",
        ["workflow_node_id"],
    )
    op.create_index(
        "ix_t_workflow_node_contract_files_contract_file_id",
        "t_workflow_node_contract_files",
        ["contract_file_id"],
    )
    _backfill()


def downgrade() -> None:
    op.drop_index(
        "ix_t_workflow_node_contract_files_contract_file_id",
        table_name="t_workflow_node_contract_files",
    )
    op.drop_index(
        "ix_t_workflow_node_contract_files_workflow_node_id",
        table_name="t_workflow_node_contract_files",
    )
    op.drop_table("t_workflow_node_contract_files")
    op.drop_index("ix_t_plan_resources_resource_id", table_name="t_plan_resources")
    op.drop_index("ix_t_plan_resources_plan_id", table_name="t_plan_resources")
    op.drop_table("t_plan_resources")
