from __future__ import annotations

import typing
import re
from dataclasses import dataclass
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


class DatabaseCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseServerInfo:
    family: str
    version: typing.Tuple[int, int, int]
    raw_version: str


def _parse_server_version(raw_version: str) -> DatabaseServerInfo:
    family = "mariadb" if "mariadb" in raw_version.casefold() else "mysql"
    versions = [
        tuple(int(part) for part in match)
        for match in re.findall(r"(\d+)\.(\d+)\.(\d+)", raw_version)
    ]
    if not versions:
        raise DatabaseCompatibilityError(
            f"无法识别数据库服务端版本：{raw_version}"
        )
    version = versions[0]
    # Newer MariaDB servers may advertise a 5.5.5 compatibility prefix.
    if family == "mariadb" and version == (5, 5, 5) and len(versions) > 1:
        version = versions[1]
    return DatabaseServerInfo(family=family, version=version, raw_version=raw_version)


def validate_database_server(connection: typing.Any) -> typing.Optional[DatabaseServerInfo]:
    """Fail early when the MySQL-compatible server cannot safely host OpenSLT."""
    if connection.dialect.name != "mysql":
        return None

    raw_version = str(connection.exec_driver_sql("SELECT VERSION()").scalar_one())
    info = _parse_server_version(raw_version)
    minimum = (5, 5, 68) if info.family == "mariadb" else (5, 5, 3)
    if info.version < minimum:
        label = "MariaDB" if info.family == "mariadb" else "MySQL"
        required = ".".join(str(part) for part in minimum)
        raise DatabaseCompatibilityError(
            f"OpenSLT 要求 {label} >= {required}，当前服务端为 {raw_version}"
        )

    innodb_support = connection.exec_driver_sql(
        "SELECT SUPPORT FROM information_schema.ENGINES WHERE ENGINE = 'InnoDB'"
    ).scalar_one_or_none()
    if str(innodb_support or "").upper() not in {"YES", "DEFAULT"}:
        raise DatabaseCompatibilityError("OpenSLT 要求数据库服务端启用 InnoDB")

    utf8mb4_collation = connection.exec_driver_sql(
        "SELECT COLLATION_NAME FROM information_schema.COLLATIONS "
        "WHERE COLLATION_NAME = 'utf8mb4_unicode_ci'"
    ).scalar_one_or_none()
    if not utf8mb4_collation:
        raise DatabaseCompatibilityError(
            "OpenSLT 要求数据库服务端支持 utf8mb4_unicode_ci"
        )

    incompatible_tables = connection.exec_driver_sql(
        "SELECT GROUP_CONCAT(CONCAT(TABLE_NAME, ':', COALESCE(ENGINE, 'NULL')) "
        "ORDER BY TABLE_NAME SEPARATOR ',') FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND LEFT(TABLE_NAME, 2) = 't_' "
        "AND UPPER(COALESCE(ENGINE, '')) <> 'INNODB'"
    ).scalar_one_or_none()
    if incompatible_tables:
        raise DatabaseCompatibilityError(
            f"OpenSLT 数据表必须全部使用 InnoDB：{incompatible_tables}"
        )
    return info


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
