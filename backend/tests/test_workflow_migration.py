from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

import app.models  # noqa: F401
from app.core.database import Base


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERSION_TABLE = "t_alembic_version"


def _database_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.as_posix()}"


def _alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = _database_url(database_path)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed


def _model_foreign_keys(table: sa.Table):
    return {
        (
            tuple(constraint.column_keys),
            constraint.elements[0].column.table.name,
            tuple(element.column.name for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.foreign_key_constraints
    }


def _database_foreign_keys(inspector: sa.Inspector, table_name: str):
    return {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            item.get("options", {}).get("ondelete"),
        )
        for item in inspector.get_foreign_keys(table_name)
    }


def test_single_baseline_migration_matches_models_and_downgrades(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.sqlite3"
    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    model_table_names = set(Base.metadata.tables)
    assert len(model_table_names) == 27
    assert all(name.startswith("t_") for name in model_table_names)
    assert set(inspector.get_table_names()) == model_table_names | {VERSION_TABLE}

    for table_name in sorted(model_table_names):
        model_table = Base.metadata.tables[table_name]
        database_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(database_columns) == set(model_table.columns.keys())
        for model_column in model_table.columns:
            database_column = database_columns[model_column.name]
            if not model_column.primary_key:
                assert database_column["nullable"] == model_column.nullable
            assert database_column["type"]._type_affinity == model_column.type._type_affinity
            if isinstance(model_column.type, sa.String):
                assert database_column["type"].length == model_column.type.length
        assert set(inspector.get_pk_constraint(table_name)["constrained_columns"]) == {
            column.name for column in model_table.primary_key.columns
        }

        model_indexes = {
            (index.name, tuple(column.name for column in index.columns), bool(index.unique))
            for index in model_table.indexes
        }
        database_indexes = {
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table_name)
        }
        assert database_indexes == model_indexes

        model_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in model_table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        database_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        assert database_uniques == model_uniques
        assert _database_foreign_keys(inspector, table_name) == _model_foreign_keys(model_table)

    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            f"SELECT version_num FROM {VERSION_TABLE}"
        ).scalar_one() == "0008"
    engine.dispose()

    _alembic(database_path, "downgrade", "base")
    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert remaining == {VERSION_TABLE}
        assert connection.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone() is None


def test_mysql_offline_migration_is_legacy_mariadb_compatible() -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = (
        "mysql+pymysql://openslt:secret@127.0.0.1:3306/openslt?charset=utf8mb4"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    sql = completed.stdout

    created_tables = re.findall(r"CREATE TABLE (t_[a-z0-9_]+)", sql)
    assert len(created_tables) == 28
    assert set(created_tables) == set(Base.metadata.tables) | {VERSION_TABLE}
    assert " LONGTEXT" in sql
    assert not re.search(r"\sJSON(?:\s|,)", sql)
    assert "filename(120), checksum(64)" in sql
    assert "idempotency_key(191)" in sql
    assert (
        "ALTER TABLE t_test_scenarios ADD CONSTRAINT "
        "fk_test_scenarios_draft_workflow_version_id"
    ) in sql


def test_durable_task_migration_resumes_after_non_transactional_ddl_failure(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "partial-durable-task.sqlite3"
    _alembic(database_path, "upgrade", "0005")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE t_durable_tasks (
                id INTEGER NOT NULL PRIMARY KEY,
                task_type VARCHAR(64) NOT NULL,
                payload TEXT NOT NULL,
                idempotency_key VARCHAR(255) NOT NULL,
                status VARCHAR(24) NOT NULL,
                attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                available_at DATETIME NOT NULL,
                lease_expires_at DATETIME,
                locked_by VARCHAR(128),
                last_error TEXT,
                created_at DATETIME NOT NULL,
                started_at DATETIME,
                finished_at DATETIME
            );
            CREATE INDEX ix_t_durable_tasks_task_type
                ON t_durable_tasks (task_type);
            """
        )

    _alembic(database_path, "upgrade", "head")
    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    indexes = {item["name"] for item in inspector.get_indexes("t_durable_tasks")}
    assert indexes == {index.name for index in Base.metadata.tables["t_durable_tasks"].indexes}
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            f"SELECT version_num FROM {VERSION_TABLE}"
        ).scalar_one() == "0008"
    engine.dispose()


def test_expected_migration_revisions_remain() -> None:
    revision_files = {
        path.name
        for path in (REPOSITORY_ROOT / "backend" / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    }
    assert revision_files == {
        "0001_initial.py",
        "0002_remove_simulated_mode.py",
        "0003_normalize_resource_relations.py",
        "0004_normalize_plan_and_contract_relations.py",
        "0005_run_state_governance.py",
        "0006_durable_tasks.py",
        "0007_resource_wiring_profile.py",
        "0008_rem_more_config.py",
    }


def test_rem_more_config_migration_preserves_existing_resources(tmp_path: Path) -> None:
    database_path = tmp_path / "existing-rem.sqlite3"
    _alembic(database_path, "upgrade", "0007")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO t_resources (
                name, resource_type, business_code, host, ssh_port, username, auth_type,
                database_tls_enabled, remote_path, capabilities, wiring_profile,
                version_info, notes, is_enabled, is_deleted, health_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Existing REM", "rem", "fut_mm", "10.1.51.8", 22, "root", "password",
                False, "/home/user0/rem_mm", "{}", '{"client_interface": {}}',
                "", "legacy", True, False, "unknown",
                "2026-01-01 00:00:00", "2026-01-01 00:00:00",
            ),
        )

    _alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(t_resources)")}
        assert "wiring_profile" not in columns
        assert {"trade_ip", "trade_tcp_port", "trade_udp_port", "query_ip", "query_port"} <= columns
        row = connection.execute(
            """
            SELECT name, trade_ip, trade_tcp_port, trade_udp_port, query_ip, query_port
            FROM t_resources
            """
        ).fetchone()
        assert row == ("Existing REM", None, None, None, None, None)


def test_portable_launcher_applies_baseline_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "portable.sqlite3"
    environment = dict(os.environ)
    environment["DATABASE_URL"] = _database_url(database_path)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "backend")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from portable_main import upgrade_portable_database; upgrade_portable_database()",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert tables == set(Base.metadata.tables) | {VERSION_TABLE}
        assert connection.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone() == (
            "0008",
        )


def test_resource_relation_migration_backfills_legacy_json_in_order(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-resources.sqlite3"
    _alembic(database_path, "upgrade", "0002")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO t_users (id, username, display_name, password_hash, role, is_active, created_at, updated_at) "
            "VALUES (1, 'admin', 'Admin', 'hash', 'admin', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        for resource_id, resource_type in ((10, "rem"), (20, "market")):
            connection.execute(
                "INSERT INTO t_resources "
                "(id, name, resource_type, business_code, host, ssh_port, username, auth_type, "
                "remote_path, capabilities, version_info, notes, is_enabled, is_deleted, health_status, "
                "database_tls_enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, 'fut_mm', '127.0.0.1', 22, 'tester', 'password', '', '{}', '', '', 1, 0, 'unknown', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (resource_id, f"resource-{resource_id}", resource_type),
            )
        connection.execute(
            "INSERT INTO t_test_plans "
            "(id, name, business_code, description, default_resource_ids, config_version, is_enabled, created_by, created_at, updated_at) "
            "VALUES (1, 'plan', 'fut_mm', '', '[20, 10]', '1.0', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO t_test_scenarios "
            "(id, plan_id, name, scenario_type, config_version, expected_artifacts, default_resource_ids, "
            "required_resource_types, is_enabled, workflow_status, is_archived, created_at, updated_at) "
            "VALUES (1, 1, 'scenario', 'order', '1.0', '[]', '[20, 10]', '[\"market\", \"rem\"]', 1, 'draft', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO t_scenario_workflow_versions "
            "(id, scenario_id, version_no, status, revision, resource_ids, created_by, created_at, updated_at) "
            "VALUES (1, 1, 1, 'published', 1, '[10, 20]', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO t_test_runs "
            "(id, run_number, plan_id, scenario_id, workflow_version_id, business_code, status, progress, "
            "resource_ids, config_snapshot, trace_id, created_by, logs_complete, created_at, updated_at) "
            "VALUES (1, 'R1', 1, 1, 1, 'fut_mm', 'draft', 0, '[20, 10]', '{}', 'trace', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO t_scenario_workflow_nodes "
            "(id, workflow_version_id, node_key, position, node_type, name, config, created_at, updated_at) "
            "VALUES (1, 1, 'order-node', 1, 'order_preparation', 'order', "
            "'{\"contract_file_ids\": [5]}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO t_contract_data_files "
            "(id, scenario_id, workflow_node_id, order_resource_id, contract_type, source_table, "
            "filename, remote_path, archive_path, row_count, size, checksum, preview_rows, created_by, created_at) "
            "VALUES (5, 1, 1, 10, 'futures', 't_close_report', 'contracts.csv', '/tmp/contracts.csv', "
            "'/tmp/contracts.csv', 1, 10, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            "'[]', 1, CURRENT_TIMESTAMP)"
        )
        connection.commit()

    _alembic(database_path, "upgrade", "0003")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT resource_id, position FROM t_scenario_resources ORDER BY position"
        ).fetchall() == [(20, 1), (10, 2)]
        assert connection.execute(
            "SELECT resource_id, position FROM t_workflow_version_resources ORDER BY position"
        ).fetchall() == [(10, 1), (20, 2)]
        assert connection.execute(
            "SELECT resource_id, position FROM t_run_resources ORDER BY position"
        ).fetchall() == [(20, 1), (10, 2)]

    _alembic(database_path, "upgrade", "0004")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT resource_id, position FROM t_plan_resources ORDER BY position"
        ).fetchall() == [(20, 1), (10, 2)]
        assert connection.execute(
            "SELECT contract_file_id, position FROM t_workflow_node_contract_files ORDER BY position"
        ).fetchall() == [(5, 1)]
