from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime

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
    assert len(model_table_names) == 42
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
        ).scalar_one() == "0018"
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


def test_durable_task_run_id_migration_resumes_and_backfills(tmp_path: Path) -> None:
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
        connection.execute("ALTER TABLE t_durable_tasks ADD COLUMN run_id INTEGER")
        connection.execute(
            "CREATE INDEX ix_t_durable_tasks_run_id ON t_durable_tasks (run_id)"
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


def test_model_management_migration_preserves_existing_configuration(tmp_path: Path) -> None:
    database_path = tmp_path / "model-management.sqlite3"
    _alembic(database_path, "upgrade", "0011")
    timestamp = "2026-09-03 00:00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO t_svn_knowledge_sources "
            "(repository_url, repository_urls, username, encrypted_password, "
            "embedding_base_url, embedding_model, encrypted_embedding_api_key, "
            "allow_insecure_embedding_http, embedding_dimensions, llm_base_url, llm_model, "
            "encrypted_llm_api_key, allow_insecure_llm_http, include_paths, "
            "sync_interval_minutes, enabled, allow_insecure_http, sync_status, "
            "last_attempt_at, last_success_at, last_revisions, file_count, failed_file_count, "
            "last_changes, last_error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "https://svn.example/knowledge", '["https://svn.example/knowledge"]',
                "readonly", "svn-encrypted", "https://embedding.example/v1", "bge-m3",
                "embedding-encrypted", 0, 1024, "https://chat.example/v1", "qwen3",
                "chat-encrypted", 0, '["docs"]', 30, 1, 0, "succeeded", None, timestamp,
                '{"docs":"12"}', 1, 0, "{}", None, timestamp, timestamp,
            ),
        )
        connection.commit()

    _alembic(database_path, "upgrade", "0012")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT kind, model_id FROM t_ai_models ORDER BY kind"
        ).fetchall() == [("chat", "qwen3"), ("embedding", "bge-m3")]
        assert connection.execute(
            "SELECT kind FROM t_active_ai_models ORDER BY kind"
        ).fetchall() == [("chat",), ("embedding",)]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(t_svn_knowledge_sources)")
        }
        assert "llm_model" not in columns and "embedding_model" not in columns

    _alembic(database_path, "downgrade", "0011")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT embedding_base_url, embedding_model, encrypted_embedding_api_key, "
            "llm_base_url, llm_model, encrypted_llm_api_key FROM t_svn_knowledge_sources"
        ).fetchone() == (
            "https://embedding.example/v1", "bge-m3", "embedding-encrypted",
            "https://chat.example/v1", "qwen3", "chat-encrypted",
        )


def test_smart_case_migration_resumes_when_mysql_ddl_outlives_revision_stamp(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "resumed-smart-case.sqlite3"
    _alembic(database_path, "upgrade", "0011")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"UPDATE {VERSION_TABLE} SET version_num = '0010' WHERE version_num = '0011'"
        )
        connection.commit()

    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {VERSION_TABLE}
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            f"SELECT version_num FROM {VERSION_TABLE}"
        ).scalar_one() == "0018"
    engine.dispose()


def test_model_management_migration_resumes_after_provider_table_creation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "resumed-model-management.sqlite3"
    _alembic(database_path, "upgrade", "0011")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE t_model_providers ("
            "id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(128) NOT NULL UNIQUE, "
            "base_url VARCHAR(1024) NOT NULL, encrypted_api_key TEXT, "
            "allow_insecure_http BOOLEAN NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
        connection.commit()

    _alembic(database_path, "upgrade", "head")

    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {VERSION_TABLE}
    model_columns = {
        column["name"]: column for column in inspector.get_columns("t_ai_models")
    }
    assert model_columns["model_id"]["type"].length == 160
    engine.dispose()

    _alembic(database_path, "downgrade", "0012")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"UPDATE {VERSION_TABLE} SET version_num = '0011' WHERE version_num = '0012'"
        )
        connection.commit()
    _alembic(database_path, "upgrade", "head")


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
    assert len(created_tables) == 43
    assert set(created_tables) == set(Base.metadata.tables) | {VERSION_TABLE}
    assert " LONGTEXT" in sql
    assert not re.search(r"\sJSON(?:\s|,)", sql)
    assert sql.count("ENGINE=InnoDB") == 42
    assert sql.count("CHARSET=utf8mb4") == 42
    assert sql.count("COLLATE utf8mb4_unicode_ci") == 42
    assert "filename(120), checksum(64)" in sql
    assert "idempotency_key(191)" in sql
    assert "model_id VARCHAR(160) NOT NULL" in sql
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
        "0011_multiple_svn_repositories.py",
        "0012_model_management.py",
        "0013_case_generation_prompts.py",
        "0015_chat.py",
        "0016_account_models.py",
        "0017_embedding_dimensions.py",
        "0018_knowledge_bases.py",
        "0014_user_llm_configs.py",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "0018 (head)"


@pytest.mark.parametrize("admin_personal", [False, True])
def test_account_models_migrate_credentials_and_preserve_model_ids(tmp_path: Path, admin_personal: bool) -> None:
    database_path = tmp_path / "account-models.sqlite3"
    _alembic(database_path, "upgrade", "0015")
    engine = sa.create_engine(_database_url(database_path))
    metadata = sa.MetaData()
    metadata.reflect(engine)
    timestamp = datetime(2026, 9, 9)
    with engine.begin() as connection:
        connection.execute(metadata.tables['t_users'].insert(), [
            dict(id=i, username='user%s' % i, display_name='', password_hash='hash', role=role,
                 is_active=True, created_at=timestamp, updated_at=timestamp)
            for i, role in [(1, 'admin'), (2, 'tester'), (3, 'admin')]
        ])
        connection.execute(metadata.tables['t_model_providers'].insert(), dict(
            id=1, name='共享服务', base_url='https://shared.example/v1', encrypted_api_key='shared-ciphertext',
            allow_insecure_http=False, created_at=timestamp, updated_at=timestamp))
        connection.execute(metadata.tables['t_ai_models'].insert(), [dict(
            id=i, provider_id=1, kind=kind, model_id=kind + '-model', created_at=timestamp, updated_at=timestamp)
            for i, kind in [(1, 'chat'), (2, 'embedding')]])
        connection.execute(metadata.tables['t_active_ai_models'].insert(), [dict(kind='chat', model_id=1), dict(kind='embedding', model_id=2)])
        connection.execute(metadata.tables['t_svn_knowledge_sources'].insert(), dict(
            repository_url='https://svn.example/repo', repository_urls='["https://svn.example/repo"]',
            username='', encrypted_password='', embedding_dimensions=768, include_paths='[]',
            sync_interval_minutes=30, enabled=True, allow_insecure_http=False, sync_status='succeeded',
            last_revisions='{}', file_count=1, failed_file_count=0, last_changes='{}',
            created_at=timestamp, updated_at=timestamp))
        connection.execute(metadata.tables['t_user_llm_configs'].insert(), [dict(
            user_id=i, base_url='https://personal%s.example/v1' % i, model_id='personal-%s' % i,
            encrypted_api_key='ciphertext-%s' % i, allow_insecure_http=False, created_at=timestamp, updated_at=timestamp)
            for i in ([1, 2] if admin_personal else [2])])
    engine.dispose()

    _alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT kind, model_id FROM t_active_ai_models').fetchall() == [('embedding', 2)]
        assert connection.execute('SELECT user_id, encrypted_api_key FROM t_model_providers WHERE id=1').fetchone() == (None, 'shared-ciphertext')
        assert connection.execute('SELECT embedding_dimensions FROM t_model_providers WHERE id=1').fetchone() == (768,)
        assert connection.execute('SELECT DISTINCT embedding_dimensions FROM t_model_providers WHERE user_id IS NOT NULL').fetchall() == [(1024,)]
        assert connection.execute('SELECT p.user_id FROM t_ai_models m JOIN t_model_providers p ON p.id=m.provider_id WHERE m.id=1').fetchone() == (1,)
        selected = connection.execute(
            'SELECT s.user_id, m.model_id, p.user_id, p.encrypted_api_key FROM t_user_chat_models s '
            'JOIN t_ai_models m ON m.id=s.model_id JOIN t_model_providers p ON p.id=m.provider_id ORDER BY s.user_id'
        ).fetchall()
        assert selected == [(1, 'personal-1' if admin_personal else 'chat-model', 1,
                              'ciphertext-1' if admin_personal else 'shared-ciphertext'),
                            (2, 'personal-2', 2, 'ciphertext-2')]
    _alembic(database_path, "downgrade", "0015")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute('SELECT user_id, encrypted_api_key FROM t_user_llm_configs ORDER BY user_id').fetchall() == [
            (1, 'ciphertext-1' if admin_personal else 'shared-ciphertext'), (2, 'ciphertext-2')]
        assert connection.execute('SELECT id FROM t_ai_models WHERE id IN (1,2) ORDER BY id').fetchall() == [(1,), (2,)]


def test_migration_commits_revision_after_preflight_queries(tmp_path: Path, monkeypatch) -> None:
    from alembic import command
    from alembic.config import Config
    from app.core import database
    from app.core.config import settings

    database_path = tmp_path / "preflight.sqlite3"
    monkeypatch.setattr(settings, "database_url", _database_url(database_path))
    monkeypatch.setattr(
        database, "validate_database_server",
        lambda connection: connection.exec_driver_sql("SELECT 1").scalar_one(),
    )
    command.upgrade(Config(str(REPOSITORY_ROOT / "alembic.ini")), "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone() == ("0018",)


def test_prompt_migration_resumes_without_losing_saved_prompts(tmp_path: Path) -> None:
    database_path = tmp_path / "resumed-prompts.sqlite3"
    _alembic(database_path, "upgrade", "0013")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO t_case_generation_prompts (id, system_prompt, user_prompt) "
            "VALUES (1, 'saved system', 'saved user')"
        )
        connection.execute(f"UPDATE {VERSION_TABLE} SET version_num = '0012'")
        connection.commit()

    _alembic(database_path, "upgrade", "head")
    _alembic(database_path, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(f"SELECT version_num FROM {VERSION_TABLE}").fetchone() == ("0018",)
        assert connection.execute("SELECT * FROM t_case_generation_prompts").fetchall() == [
            (1, "saved system", "saved user")
        ]


@pytest.mark.parametrize("interruption", [None, "base_table", "upload_table", "partial_constraints", "backfill"])
def test_knowledge_base_migration_binds_existing_sources_and_consumers(tmp_path: Path, interruption) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.models import ActiveAiModel, AiModel, ChatConversation, ModelProvider, SmartCaseGeneration, SvnKnowledgeSource, User
    database_path = tmp_path / "knowledge-base-migration.sqlite3"
    _alembic(database_path, "upgrade", "0017")
    engine = sa.create_engine(_database_url(database_path))
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(id=1, username="admin", password_hash="hash", role="admin"))
        connection.execute(ModelProvider.__table__.insert().values(id=1, name="嵌入服务", base_url="https://embedding.example/v1", embedding_dimensions=768))
        connection.execute(AiModel.__table__.insert().values(id=1, provider_id=1, kind="embedding", model_id="embed"))
        connection.execute(ActiveAiModel.__table__.insert().values(kind="embedding", model_id=1))
        connection.execute(SvnKnowledgeSource.__table__.insert().values(id=1, repository_url="https://svn.example/docs", repository_urls=["https://svn.example/docs"], username="reader", encrypted_password="preserved-encrypted-password", include_paths=["requirements"]))
        connection.execute(ChatConversation.__table__.insert(), [dict(id=1, user_id=1, mode="knowledge"), dict(id=2, user_id=1, mode="general")])
        connection.execute(SmartCaseGeneration.__table__.insert().values(id=1, requirement_path="REQ-123.md", requirement_revision="123", requirement_name="权限", llm_model="chat", created_by=1))
        # Persist individual DDL steps, as MySQL does before a failed revision is stamped.
        if interruption:
            Base.metadata.tables["t_knowledge_bases"].create(connection)
        if interruption in ("upload_table", "partial_constraints", "backfill"):
            Base.metadata.tables["t_knowledge_uploads"].create(connection)
            connection.exec_driver_sql("DROP INDEX ix_t_knowledge_uploads_knowledge_base_id")
        if interruption in ("partial_constraints", "backfill"):
            operations = Operations(MigrationContext.configure(connection))
            for table in ("t_svn_knowledge_sources", "t_smart_case_generations", "t_chat_conversations"):
                with operations.batch_alter_table(table) as batch:
                    batch.add_column(sa.Column("knowledge_base_id", sa.Integer(), nullable=True))
                    if table == "t_smart_case_generations":
                        batch.create_foreign_key("fk_" + table + "_knowledge_base", "t_knowledge_bases", ["knowledge_base_id"], ["id"], ondelete="RESTRICT")
                    if table == "t_chat_conversations":
                        batch.create_index("ix_" + table + "_knowledge_base_id", ["knowledge_base_id"])
        if interruption == "backfill":
            connection.execute(Base.metadata.tables["t_knowledge_bases"].insert().values(id=1, name="默认知识库", embedding_model_id=1, legacy_index=True))
    engine.dispose()
    _alembic(database_path, "upgrade", "head")
    _alembic(database_path, "upgrade", "head")
    engine = sa.create_engine(_database_url(database_path))
    inspector = sa.inspect(engine)
    for table_name in ("t_knowledge_bases", "t_knowledge_uploads", "t_svn_knowledge_sources", "t_smart_case_generations", "t_chat_conversations"):
        model_table = Base.metadata.tables[table_name]
        assert _database_foreign_keys(inspector, table_name) == _model_foreign_keys(model_table)
        assert {(i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table_name)} == {
            (i.name, tuple(c.name for c in i.columns), bool(i.unique)) for i in model_table.indexes
        }
    engine.dispose()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT name, embedding_model_id, chunk_size, chunk_overlap, legacy_index FROM t_knowledge_bases").fetchall() == [("默认知识库", 1, 1200, 150, 1)]
        assert connection.execute("SELECT knowledge_base_id, encrypted_password FROM t_svn_knowledge_sources").fetchone() == (1, "preserved-encrypted-password")
        assert connection.execute("SELECT knowledge_base_id FROM t_chat_conversations ORDER BY id").fetchall() == [(1,), (None,)]
        assert connection.execute("SELECT knowledge_base_id FROM t_smart_case_generations").fetchone() == (1,)
    _alembic(database_path, "downgrade", "0017")
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT encrypted_password FROM t_svn_knowledge_sources").fetchone() == ("preserved-encrypted-password",)


def test_knowledge_base_migration_replay_preserves_saved_data(tmp_path: Path) -> None:
    from app.models import ChatConversation, KnowledgeBase, KnowledgeUpload, SmartCaseGeneration, SvnKnowledgeSource, User

    database_path = tmp_path / "knowledge-base-replay.sqlite3"
    _alembic(database_path, "upgrade", "head")
    engine = sa.create_engine(_database_url(database_path))
    with engine.begin() as connection:
        connection.execute(User.__table__.insert().values(id=1, username="admin", password_hash="hash", role="admin"))
        connection.execute(KnowledgeBase.__table__.insert(), [
            dict(id=1, name="已编辑知识库", description="保留描述", chunk_size=900, chunk_overlap=100, top_k=7),
            dict(id=2, name="另一个知识库", description="", chunk_size=1200, chunk_overlap=150, top_k=10),
        ])
        connection.execute(SvnKnowledgeSource.__table__.insert().values(id=1, knowledge_base_id=2, repository_url="https://svn.example/docs", username="reader", encrypted_password="saved-password"))
        connection.execute(ChatConversation.__table__.insert(), [dict(id=1, user_id=1, mode="knowledge", knowledge_base_id=2), dict(id=2, user_id=1, mode="general", knowledge_base_id=None)])
        connection.execute(SmartCaseGeneration.__table__.insert().values(id=1, knowledge_base_id=2, requirement_path="REQ-123.md", requirement_revision="123", requirement_name="权限", llm_model="chat", created_by=1))
        connection.execute(KnowledgeUpload.__table__.insert().values(id=1, knowledge_base_id=2, name="需求.md", storage_name="saved-file", size=10, sha256="digest"))
        connection.exec_driver_sql(f"UPDATE {VERSION_TABLE} SET version_num = '0017'")
        before = {table: connection.exec_driver_sql("SELECT * FROM " + table).fetchall() for table in (
            "t_knowledge_bases", "t_knowledge_uploads", "t_svn_knowledge_sources", "t_chat_conversations", "t_smart_case_generations",
        )}
    _alembic(database_path, "upgrade", "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(f"SELECT version_num FROM {VERSION_TABLE}").scalar_one() == "0018"
        for table, rows in before.items():
            assert connection.exec_driver_sql("SELECT * FROM " + table).fetchall() == rows
    engine.dispose()
