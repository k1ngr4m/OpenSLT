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
    assert len(model_table_names) == 20
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
        ).scalar_one() == "0001"
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
    assert len(created_tables) == 21
    assert set(created_tables) == set(Base.metadata.tables) | {VERSION_TABLE}
    assert " LONGTEXT" in sql
    assert not re.search(r"\sJSON(?:\s|,)", sql)
    assert "filename(120), checksum(64)" in sql
    assert (
        "ALTER TABLE t_test_scenarios ADD CONSTRAINT "
        "fk_test_scenarios_draft_workflow_version_id"
    ) in sql


def test_only_single_baseline_revision_remains() -> None:
    revision_files = {
        path.name
        for path in (REPOSITORY_ROOT / "backend" / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    }
    assert revision_files == {"0001_initial.py"}


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
            "0001",
        )
