"""Separate model providers and models from the SVN knowledge source.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _mysql_options() -> dict:
    if op.get_context().dialect.name != "mysql":
        return {}
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
    }


def _migrate_existing_models() -> None:
    if getattr(op.get_context(), "as_sql", False):
        return
    connection = op.get_bind()
    source_columns = {
        column["name"]
        for column in sa.inspect(connection).get_columns("t_svn_knowledge_sources")
    }
    legacy_columns = {
        "embedding_base_url",
        "embedding_model",
        "encrypted_embedding_api_key",
        "allow_insecure_embedding_http",
        "llm_base_url",
        "llm_model",
        "encrypted_llm_api_key",
        "allow_insecure_llm_http",
    }
    if not legacy_columns.issubset(source_columns):
        return
    source = connection.execute(
        sa.text(
            "SELECT embedding_base_url, embedding_model, encrypted_embedding_api_key, "
            "allow_insecure_embedding_http, llm_base_url, llm_model, "
            "encrypted_llm_api_key, allow_insecure_llm_http "
            "FROM t_svn_knowledge_sources ORDER BY id LIMIT 1"
        )
    ).mappings().first()
    if source is None:
        return
    now = datetime.now()
    configurations = (
        (
            "embedding",
            "原有 Embedding 提供商",
            source["embedding_base_url"],
            source["embedding_model"],
            source["encrypted_embedding_api_key"],
            source["allow_insecure_embedding_http"],
        ),
        (
            "chat",
            "原有对话模型提供商",
            source["llm_base_url"],
            source["llm_model"],
            source["encrypted_llm_api_key"],
            source["allow_insecure_llm_http"],
        ),
    )
    for kind, name, base_url, model_name, encrypted_api_key, allow_http in configurations:
        if not base_url or not model_name:
            continue
        model_id = connection.execute(
            sa.text("SELECT model_id FROM t_active_ai_models WHERE kind = :kind"),
            {"kind": kind},
        ).scalar()
        if model_id is None:
            provider_id = connection.execute(
                sa.text("SELECT id FROM t_model_providers WHERE name = :name"),
                {"name": name},
            ).scalar()
            if provider_id is None:
                provider_result = connection.execute(
                    sa.text(
                        "INSERT INTO t_model_providers "
                        "(name, base_url, encrypted_api_key, allow_insecure_http, created_at, updated_at) "
                        "VALUES (:name, :base_url, :encrypted_api_key, :allow_http, :now, :now)"
                    ),
                    {
                        "name": name,
                        "base_url": base_url,
                        "encrypted_api_key": encrypted_api_key,
                        "allow_http": allow_http,
                        "now": now,
                    },
                )
                provider_id = provider_result.lastrowid
            model_id = connection.execute(
                sa.text(
                    "SELECT id FROM t_ai_models "
                    "WHERE provider_id = :provider_id AND kind = :kind AND model_id = :model_id"
                ),
                {"provider_id": provider_id, "kind": kind, "model_id": model_name},
            ).scalar()
            if model_id is None:
                model_result = connection.execute(
                    sa.text(
                        "INSERT INTO t_ai_models "
                        "(provider_id, kind, model_id, created_at, updated_at) "
                        "VALUES (:provider_id, :kind, :model_id, :now, :now)"
                    ),
                    {
                        "provider_id": provider_id,
                        "kind": kind,
                        "model_id": model_name,
                        "now": now,
                    },
                )
                model_id = model_result.lastrowid
            connection.execute(
                sa.text(
                    "INSERT INTO t_active_ai_models (kind, model_id) "
                    "VALUES (:kind, :model_id)"
                ),
                {"kind": kind, "model_id": model_id},
            )
        if kind == "chat":
            connection.execute(
                sa.text(
                    "UPDATE t_smart_case_generations SET ai_model_id = :model_id "
                    "WHERE ai_model_id IS NULL AND llm_model = :model_name"
                ),
                {"model_id": model_id, "model_name": model_name},
            )


def upgrade() -> None:
    context = op.get_context()
    inspector = None if getattr(context, "as_sql", False) else sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names()) if inspector else set()
    if not inspector or "t_model_providers" not in existing_tables:
        op.create_table(
            "t_model_providers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("base_url", sa.String(1024), nullable=False),
            sa.Column("encrypted_api_key", sa.Text(), nullable=True),
            sa.Column("allow_insecure_http", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
            **_mysql_options(),
        )
    ai_models_exists = inspector is not None and "t_ai_models" in existing_tables
    if not ai_models_exists:
        op.create_table(
            "t_ai_models",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("provider_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("model_id", sa.String(160), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["provider_id"], ["t_model_providers.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "provider_id", "kind", "model_id", name="uq_ai_model_provider_kind_id"
            ),
            **_mysql_options(),
        )
    ai_model_indexes = (
        {index["name"] for index in inspector.get_indexes("t_ai_models")}
        if ai_models_exists
        else set()
    )
    if "ix_t_ai_models_provider_id" not in ai_model_indexes:
        op.create_index("ix_t_ai_models_provider_id", "t_ai_models", ["provider_id"])
    if "ix_t_ai_models_kind" not in ai_model_indexes:
        op.create_index("ix_t_ai_models_kind", "t_ai_models", ["kind"])
    if not inspector or "t_active_ai_models" not in existing_tables:
        op.create_table(
            "t_active_ai_models",
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("model_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["model_id"], ["t_ai_models.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("kind"),
            sa.UniqueConstraint("model_id"),
            **_mysql_options(),
        )

    generation_columns = (
        {column["name"] for column in inspector.get_columns("t_smart_case_generations")}
        if inspector
        else set()
    )
    generation_foreign_keys = (
        inspector.get_foreign_keys("t_smart_case_generations") if inspector else []
    )
    generation_indexes = (
        {index["name"] for index in inspector.get_indexes("t_smart_case_generations")}
        if inspector
        else set()
    )
    missing_column = not inspector or "ai_model_id" not in generation_columns
    missing_foreign_key = not inspector or not any(
        item["constrained_columns"] == ["ai_model_id"]
        and item["referred_table"] == "t_ai_models"
        for item in generation_foreign_keys
    )
    missing_index = (
        not inspector
        or "ix_t_smart_case_generations_ai_model_id" not in generation_indexes
    )
    if missing_column or missing_foreign_key or missing_index:
        with op.batch_alter_table("t_smart_case_generations") as batch:
            if missing_column:
                batch.add_column(sa.Column("ai_model_id", sa.Integer(), nullable=True))
            if missing_foreign_key:
                batch.create_foreign_key(
                    "fk_smart_case_generations_ai_model_id",
                    "t_ai_models",
                    ["ai_model_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if missing_index:
                batch.create_index("ix_t_smart_case_generations_ai_model_id", ["ai_model_id"])

    _migrate_existing_models()

    legacy_columns = (
        "allow_insecure_llm_http",
        "encrypted_llm_api_key",
        "llm_model",
        "llm_base_url",
        "allow_insecure_embedding_http",
        "encrypted_embedding_api_key",
        "embedding_model",
        "embedding_base_url",
    )
    source_columns = (
        {column["name"] for column in inspector.get_columns("t_svn_knowledge_sources")}
        if inspector
        else set(legacy_columns)
    )
    columns_to_drop = [name for name in legacy_columns if name in source_columns]
    if columns_to_drop:
        with op.batch_alter_table("t_svn_knowledge_sources") as batch:
            for name in columns_to_drop:
                batch.drop_column(name)


def _restore_active_models() -> None:
    if getattr(op.get_context(), "as_sql", False):
        return
    connection = op.get_bind()
    for kind, prefix in (("embedding", "embedding"), ("chat", "llm")):
        row = connection.execute(
            sa.text(
                "SELECT p.base_url, p.encrypted_api_key, p.allow_insecure_http, m.model_id "
                "FROM t_active_ai_models a "
                "JOIN t_ai_models m ON m.id = a.model_id "
                "JOIN t_model_providers p ON p.id = m.provider_id "
                "WHERE a.kind = :kind"
            ),
            {"kind": kind},
        ).mappings().first()
        if row:
            connection.execute(
                sa.text(
                    "UPDATE t_svn_knowledge_sources SET "
                    f"{prefix}_base_url = :base_url, {prefix}_model = :model_id, "
                    f"encrypted_{prefix}_api_key = :api_key, "
                    f"allow_insecure_{prefix}_http = :allow_http"
                ),
                {
                    "base_url": row["base_url"],
                    "model_id": row["model_id"],
                    "api_key": row["encrypted_api_key"],
                    "allow_http": row["allow_insecure_http"],
                },
            )


def downgrade() -> None:
    with op.batch_alter_table("t_svn_knowledge_sources") as batch:
        batch.add_column(sa.Column("embedding_base_url", sa.String(1024), nullable=False, server_default=""))
        batch.add_column(sa.Column("embedding_model", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("encrypted_embedding_api_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("allow_insecure_embedding_http", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("llm_base_url", sa.String(1024), nullable=False, server_default=""))
        batch.add_column(sa.Column("llm_model", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("encrypted_llm_api_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("allow_insecure_llm_http", sa.Boolean(), nullable=False, server_default=sa.false()))

    _restore_active_models()

    with op.batch_alter_table("t_smart_case_generations") as batch:
        batch.drop_index("ix_t_smart_case_generations_ai_model_id")
        batch.drop_constraint("fk_smart_case_generations_ai_model_id", type_="foreignkey")
        batch.drop_column("ai_model_id")
    op.drop_table("t_active_ai_models")
    op.drop_index("ix_t_ai_models_kind", table_name="t_ai_models")
    op.drop_index("ix_t_ai_models_provider_id", table_name="t_ai_models")
    op.drop_table("t_ai_models")
    op.drop_table("t_model_providers")
