from __future__ import annotations

import typing
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


class DatabaseBootstrapError(RuntimeError):
    pass


def ensure_database_exists(database_url: str) -> bool:
    """Create the configured MySQL database when it does not exist yet."""
    url = make_url(database_url)
    if url.get_backend_name() != "mysql":
        return False
    database_name = url.database
    if not database_name:
        raise DatabaseBootstrapError("MySQL DATABASE_URL must include a database name.")

    server_url = URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
        query=url.query,
    )
    server_engine = create_engine(
        server_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with server_engine.connect() as connection:
            exists = connection.execute(
                text(
                    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "
                    "WHERE SCHEMA_NAME = :database_name"
                ),
                {"database_name": database_name},
            ).scalar_one_or_none()
            if exists:
                return False
            quoted_name = server_engine.dialect.identifier_preparer.quote(database_name)
            connection.exec_driver_sql(
                f"CREATE DATABASE IF NOT EXISTS {quoted_name} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            return True
    except SQLAlchemyError as exc:
        raise DatabaseBootstrapError(
            f"MySQL database '{database_name}' does not exist and could not be created. "
            "Grant CREATE permission to the configured account or create it manually."
        ) from exc
    finally:
        server_engine.dispose()


engine_options = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> typing.Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
