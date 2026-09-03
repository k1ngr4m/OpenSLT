from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
import pytest

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


def test_migration_chain_matches_models_and_downgrades(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.sqlite3"
    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    model_table_names = set(Base.metadata.tables)
    assert len(model_table_names) == 31
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
        ).scalar_one() == "0010"
    engine.dispose()

    _alembic(database_path, "downgrade", "base")
    with sqlite3.connect(database_path) as connection:
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert remaining == {VERSION_TABLE}
        assert connection.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone() is None


def test_plan_directory_migration_backfills_existing_plans(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.sqlite3"
    _alembic(database_path, "upgrade", "0004")

    with sqlite3.connect(database_path) as connection:
        timestamp = "2026-07-30 00:00:00"
        connection.execute(
            "INSERT INTO t_users "
            "(id, username, display_name, password_hash, role, is_active, last_login_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "migration-user", "迁移用户", "hash", "admin", 1, None, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO t_test_plans "
            "(id, name, business_code, description, default_resource_ids, config_version, "
            "is_enabled, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "已有方案", "fut_mm", "", "[]", "1.0", 1, 1, timestamp, timestamp),
        )
        connection.commit()

    _alembic(database_path, "upgrade", "head")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT id, name, is_default FROM t_plan_directories"
        ).fetchall() == [(1, "默认目录", 1)]
        assert connection.execute(
            "SELECT directory_id FROM t_test_plans WHERE id = 1"
        ).fetchone() == (1,)


def test_durable_task_run_id_migration_backfills_and_indexes(tmp_path: Path) -> None:
    database_path = tmp_path / "durable-task-run-id.sqlite3"
    _alembic(database_path, "upgrade", "0006")

    with sqlite3.connect(database_path) as connection:
        timestamp = "2026-07-31 00:00:00"
        connection.execute(
            "INSERT INTO t_users "
            "(id, username, display_name, password_hash, role, is_active, last_login_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "migration-user", "迁移用户", "hash", "admin", 1, None, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO t_test_plans "
            "(id, directory_id, name, business_code, description, default_resource_ids, config_version, "
            "is_enabled, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "已有方案", "fut_mm", "", "[]", "1.0", 1, 1, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO t_test_scenarios "
            "(id, plan_id, name, scenario_type, config_version, expected_artifacts, "
            "default_resource_ids, required_resource_types, is_enabled, workflow_status, "
            "draft_workflow_version_id, published_workflow_version_id, is_archived, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "已有场景", "order", "1.0", "[]", "[]", "[]", 1, "draft", None, None, 0, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO t_test_runs "
            "(id, run_number, plan_id, scenario_id, workflow_version_id, business_code, status, "
            "status_version, progress, resource_ids, config_snapshot, trace_id, created_by, "
            "started_at, finished_at, timeout_at, error_code, error_message, queue_reason, "
            "paused_from, logs_complete, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "R20260731000000-MIG", 1, 1, None, "fut_mm", "resource_queue", 0, 0, "[]", "{}", "trace", 1, None, None, None, None, None, None, None, 1, timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO t_durable_tasks "
            "(id, task_type, payload, idempotency_key, status, attempts, max_attempts, "
            "available_at, lease_expires_at, locked_by, last_error, created_at, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "start_run", '{"run_id":1}', "migration:start:1", "queued", 0, 3, timestamp, None, None, None, timestamp, None, None),
        )
        connection.commit()

    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    durable_indexes = {index["name"] for index in inspector.get_indexes("t_durable_tasks")}
    log_indexes = {index["name"] for index in inspector.get_indexes("t_log_records")}
    audit_indexes = {index["name"] for index in inspector.get_indexes("t_audit_logs")}
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT run_id FROM t_durable_tasks WHERE id = 1"
        ).fetchone() == (1,)
    engine.dispose()
    assert "ix_t_durable_tasks_run_id" in durable_indexes
    assert "ix_t_durable_tasks_task_status_run" in durable_indexes
    assert "ix_t_log_records_log_type_created" in log_indexes
    assert "ix_t_audit_logs_action_created" in audit_indexes


def test_artifact_idempotency_key_migration_is_nullable_and_unique(tmp_path: Path) -> None:
    database_path = tmp_path / "artifact-idempotency.sqlite3"
    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("t_artifacts")}
    uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("t_artifacts")
    }
    assert columns["idempotency_key"]["nullable"] is True
    assert ("idempotency_key",) in uniques
    engine.dispose()

    with sqlite3.connect(database_path) as connection:
        values = (
            1, None, "statistics_analysis_json", "statistics-analysis-v001.json",
            "/tmp/statistics-analysis-v001.json", "application/json", 2, "aa", 1,
            "2026-08-10 00:00:00",
        )
        connection.execute(
            "INSERT INTO t_artifacts "
            "(run_id, step_id, artifact_type, name, path, content_type, size, checksum, "
            "is_immutable, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            values,
        )
        connection.execute(
            "INSERT INTO t_artifacts "
            "(run_id, step_id, artifact_type, name, path, content_type, size, checksum, "
            "is_immutable, created_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (1, None, "web_report", "same-name.html", "/tmp/a.html", "text/html", 2, "bb", 1, "2026-08-10 00:00:00"),
        )
        connection.execute(
            "UPDATE t_artifacts SET idempotency_key = 'statistics-analysis:1:2:1' WHERE id = 1"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE t_artifacts SET idempotency_key = 'statistics-analysis:1:2:1' WHERE id = 2"
            )


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
    assert len(created_tables) == 32
    assert set(created_tables) == set(Base.metadata.tables) | {VERSION_TABLE}
    assert " LONGTEXT" in sql
    assert not re.search(r"\sJSON(?:\s|,)", sql)
    assert sql.count("ENGINE=InnoDB") == 31
    assert sql.count("CHARSET=utf8mb4") == 31
    assert sql.count("COLLATE utf8mb4_unicode_ci") == 31
    assert "filename(120), checksum(64)" in sql
    assert "idempotency_key(191)" in sql
    assert (
        "ALTER TABLE t_test_scenarios ADD CONSTRAINT "
        "fk_test_scenarios_draft_workflow_version_id"
    ) in sql
    assert "ALTER TABLE t_durable_tasks ADD COLUMN run_id INTEGER" in sql


def test_expected_migration_revisions_remain() -> None:
    revision_files = {
        path.name
        for path in (REPOSITORY_ROOT / "backend" / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    }
    assert revision_files == {
        "0001_initial.py",
        "0002_database_config_templates.py",
        "0003_workflow_version_generations.py",
        "0004_observability_logs.py",
        "0005_plan_directories.py",
        "0006_capture_item_descriptions.py",
        "0007_database_operation_indexes.py",
        "0008_artifact_idempotency_key.py",
        "0009_run_comparisons.py",
        "0010_svn_knowledge_source.py",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "0010 (head)"
