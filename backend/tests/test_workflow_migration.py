from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _alembic(database_path: Path, *arguments: str) -> None:
    environment = dict(os.environ)
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_workflow_migration_handles_legacy_data_and_fresh_schema(tmp_path: Path):
    legacy_database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE resources (id INTEGER PRIMARY KEY);
            CREATE TABLE test_scenarios (
                id INTEGER PRIMARY KEY,
                is_enabled BOOLEAN NOT NULL
            );
            CREATE TABLE test_runs (
                id INTEGER PRIMARY KEY,
                scenario_id INTEGER NOT NULL
            );
            CREATE TABLE run_steps (id INTEGER PRIMARY KEY);
            CREATE TABLE artifacts (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                name TEXT NOT NULL
            );
            INSERT INTO test_scenarios (id, is_enabled) VALUES (1, 1), (2, 1);
            INSERT INTO test_runs (id, scenario_id) VALUES (10, 2);
            INSERT INTO artifacts (id, run_id, name) VALUES (20, 10, 'historical-report.pdf');
            """
        )
    _alembic(legacy_database, "stamp", "0003")
    _alembic(legacy_database, "upgrade", "head")

    with sqlite3.connect(legacy_database) as connection:
        scenarios = connection.execute(
            "SELECT id, is_enabled, workflow_status, is_archived FROM test_scenarios ORDER BY id"
        ).fetchall()
        assert scenarios == [(2, 0, "archived", 1)]
        assert connection.execute("SELECT id, scenario_id FROM test_runs").fetchall() == [(10, 2)]
        assert connection.execute("SELECT id, run_id, name FROM artifacts").fetchall() == [
            (20, 10, "historical-report.pdf")
        ]
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "scenario_workflow_versions",
            "scenario_workflow_nodes",
            "configuration_capture_snapshots",
            "configuration_capture_items",
            "contract_data_files",
        }.issubset(tables)
        contract_foreign_keys = {
            (row[3], row[6]) for row in connection.execute(
                "PRAGMA foreign_key_list('contract_data_files')"
            )
        }
        assert ("scenario_id", "SET NULL") in contract_foreign_keys
        assert ("workflow_node_id", "SET NULL") in contract_foreign_keys
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0004",)

    fresh_database = tmp_path / "fresh.sqlite3"
    _alembic(fresh_database, "upgrade", "0003")
    _alembic(fresh_database, "upgrade", "head")
    with sqlite3.connect(fresh_database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0004",)
