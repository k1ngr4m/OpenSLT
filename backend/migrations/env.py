from __future__ import annotations
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base, ensure_database_exists, validate_database_server
from app.models import *  # noqa: F403

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        version_table="t_alembic_version",
    )
    with context.begin_transaction(): context.run_migrations()


def run_migrations_online() -> None:
    ensure_database_exists(
        settings.database_url,
        allow_create=settings.auto_create_database,
    )
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        validate_database_server(connection)
        if connection.dialect.name == "mysql":
            connection.exec_driver_sql("SET SESSION default_storage_engine=InnoDB")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="t_alembic_version",
        )
        with context.begin_transaction(): context.run_migrations()
        validate_database_server(connection)


if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
