"""Add versioned scenario workflows and node execution data.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_names(table_name: str):
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns) -> None:
    existing = {index["name"] for index in _inspector().get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if not _table_exists("scenario_workflow_versions"):
        op.create_table(
        "scenario_workflow_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("test_scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resource_ids", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scenario_id", "version_no", name="uq_scenario_workflow_version"),
        )
    _create_index_if_missing("scenario_workflow_versions", "ix_scenario_workflow_versions_scenario_id", ["scenario_id"])
    _create_index_if_missing("scenario_workflow_versions", "ix_scenario_workflow_versions_status", ["status"])
    _create_index_if_missing("scenario_workflow_versions", "ix_scenario_workflow_versions_created_by", ["created_by"])
    if not _table_exists("scenario_workflow_nodes"):
        op.create_table(
        "scenario_workflow_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_version_id", sa.Integer(), sa.ForeignKey("scenario_workflow_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_key", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("node_type", sa.String(40), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_version_id", "node_key", name="uq_workflow_node_key"),
        sa.UniqueConstraint("workflow_version_id", "position", name="uq_workflow_node_position"),
        )
    _create_index_if_missing("scenario_workflow_nodes", "ix_scenario_workflow_nodes_workflow_version_id", ["workflow_version_id"])
    _create_index_if_missing("scenario_workflow_nodes", "ix_scenario_workflow_nodes_node_type", ["node_type"])

    scenario_columns = _column_names("test_scenarios")
    missing_scenario_columns = [
        column for column in (
            sa.Column("workflow_status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column(
                "draft_workflow_version_id",
                sa.Integer(),
                sa.ForeignKey(
                    "scenario_workflow_versions.id",
                    name="fk_test_scenarios_draft_workflow_version_id",
                    ondelete="SET NULL",
                ),
            ),
            sa.Column(
                "published_workflow_version_id",
                sa.Integer(),
                sa.ForeignKey(
                    "scenario_workflow_versions.id",
                    name="fk_test_scenarios_published_workflow_version_id",
                    ondelete="SET NULL",
                ),
            ),
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        ) if column.name not in scenario_columns
    ]
    if missing_scenario_columns:
        with op.batch_alter_table("test_scenarios") as batch:
            for column in missing_scenario_columns:
                batch.add_column(column)
    _create_index_if_missing("test_scenarios", "ix_test_scenarios_workflow_status", ["workflow_status"])
    _create_index_if_missing("test_scenarios", "ix_test_scenarios_is_archived", ["is_archived"])

    if "workflow_version_id" not in _column_names("test_runs"):
        with op.batch_alter_table("test_runs") as batch:
            batch.add_column(sa.Column(
                "workflow_version_id",
                sa.Integer(),
                sa.ForeignKey("scenario_workflow_versions.id", name="fk_test_runs_workflow_version_id"),
            ))
    _create_index_if_missing("test_runs", "ix_test_runs_workflow_version_id", ["workflow_version_id"])
    run_step_columns = _column_names("run_steps")
    missing_run_step_columns = [
        column for column in (
            sa.Column(
                "workflow_node_id",
                sa.Integer(),
                sa.ForeignKey(
                    "scenario_workflow_nodes.id",
                    name="fk_run_steps_workflow_node_id",
                    ondelete="SET NULL",
                ),
            ),
            sa.Column("node_type", sa.String(40), nullable=False, server_default="legacy"),
            sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("result_summary", sa.JSON(), nullable=False, server_default="{}"),
        ) if column.name not in run_step_columns
    ]
    if missing_run_step_columns:
        with op.batch_alter_table("run_steps") as batch:
            for column in missing_run_step_columns:
                batch.add_column(column)
    _create_index_if_missing("run_steps", "ix_run_steps_workflow_node_id", ["workflow_node_id"])

    if not _table_exists("configuration_capture_snapshots"):
        op.create_table(
            "configuration_capture_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("test_scenarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_version_id", sa.Integer(), sa.ForeignKey("scenario_workflow_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_node_id", sa.Integer(), sa.ForeignKey("scenario_workflow_nodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("test_runs.id", ondelete="CASCADE")),
        sa.Column("run_step_id", sa.Integer(), sa.ForeignKey("run_steps.id", ondelete="CASCADE")),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("resource_id", sa.Integer(), sa.ForeignKey("resources.id"), nullable=False),
        sa.Column("database_name", sa.String(128)),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        )
    for column in ("scenario_id", "workflow_version_id", "workflow_node_id", "run_id", "run_step_id", "scope", "source_type", "resource_id", "status"):
        _create_index_if_missing(
            "configuration_capture_snapshots",
            f"ix_configuration_capture_snapshots_{column}",
            [column],
        )
    if not _table_exists("configuration_capture_items"):
        op.create_table(
            "configuration_capture_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("configuration_capture_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(128), nullable=False),
        sa.Column("item_label", sa.String(128), nullable=False),
        sa.Column("value_text", sa.Text()),
        sa.Column("source_reference", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_output", sa.Text(), nullable=False, server_default=""),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="succeeded"),
        sa.Column("error_message", sa.Text()),
        )
    _create_index_if_missing("configuration_capture_items", "ix_configuration_capture_items_snapshot_id", ["snapshot_id"])
    _create_index_if_missing("configuration_capture_items", "ix_configuration_capture_items_item_key", ["item_key"])
    _create_index_if_missing("configuration_capture_items", "ix_configuration_capture_items_status", ["status"])
    if not _table_exists("contract_data_files"):
        op.create_table(
            "contract_data_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), sa.ForeignKey("test_scenarios.id", ondelete="SET NULL")),
        sa.Column("workflow_node_id", sa.Integer(), sa.ForeignKey("scenario_workflow_nodes.id", ondelete="SET NULL")),
        sa.Column("order_resource_id", sa.Integer(), sa.ForeignKey("resources.id"), nullable=False),
        sa.Column("database_resource_id", sa.Integer(), sa.ForeignKey("resources.id")),
        sa.Column("database_name", sa.String(128)),
        sa.Column("contract_type", sa.String(16), nullable=False),
        sa.Column("source_table", sa.String(128), nullable=False, server_default=""),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("remote_path", sa.String(1024), nullable=False),
        sa.Column("archive_path", sa.String(1024), nullable=False),
        sa.Column("quote_date", sa.String(32)),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("preview_rows", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workflow_node_id", "filename", "checksum", name="uq_contract_file_node_name_checksum"
        ),
        )
    for column in ("scenario_id", "workflow_node_id", "order_resource_id", "database_resource_id", "contract_type", "quote_date", "checksum", "created_by"):
        _create_index_if_missing("contract_data_files", f"ix_contract_data_files_{column}", [column])

    connection = op.get_bind()
    runs = sa.table("test_runs", sa.column("scenario_id", sa.Integer))
    scenarios = sa.table(
        "test_scenarios",
        sa.column("id", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
        sa.column("workflow_status", sa.String),
        sa.column("is_archived", sa.Boolean),
    )
    referenced = sa.select(runs.c.scenario_id).distinct()
    connection.execute(sa.delete(scenarios).where(scenarios.c.id.not_in(referenced)))
    connection.execute(
        sa.update(scenarios).values(is_enabled=False, workflow_status="archived", is_archived=True)
    )


def downgrade() -> None:
    op.drop_table("contract_data_files")
    op.drop_table("configuration_capture_items")
    op.drop_table("configuration_capture_snapshots")
    with op.batch_alter_table("run_steps") as batch:
        batch.drop_column("result_summary")
        batch.drop_column("config_snapshot")
        batch.drop_column("node_type")
        batch.drop_column("workflow_node_id")
    with op.batch_alter_table("test_runs") as batch:
        batch.drop_column("workflow_version_id")
    with op.batch_alter_table("test_scenarios") as batch:
        batch.drop_column("is_archived")
        batch.drop_column("published_workflow_version_id")
        batch.drop_column("draft_workflow_version_id")
        batch.drop_column("workflow_status")
    op.drop_table("scenario_workflow_nodes")
    op.drop_table("scenario_workflow_versions")
