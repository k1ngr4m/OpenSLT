from __future__ import annotations

import typing

import pytest

from app.core.database import DatabaseCompatibilityError, validate_database_server


class ScalarResult:
    def __init__(self, value: typing.Any) -> None:
        self.value = value

    def scalar_one(self) -> typing.Any:
        if self.value is None:
            raise AssertionError("expected one value")
        return self.value

    def scalar_one_or_none(self) -> typing.Any:
        return self.value


class FakeDialect:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeConnection:
    def __init__(
        self,
        version: str = "5.5.68-MariaDB",
        innodb_support: typing.Optional[str] = "DEFAULT",
        collation: typing.Optional[str] = "utf8mb4_unicode_ci",
        incompatible_tables: typing.Optional[str] = None,
        dialect: str = "mysql",
    ) -> None:
        self.dialect = FakeDialect(dialect)
        self.version = version
        self.innodb_support = innodb_support
        self.collation = collation
        self.incompatible_tables = incompatible_tables
        self.statements: typing.List[str] = []

    def exec_driver_sql(self, statement: str) -> ScalarResult:
        self.statements.append(statement)
        if statement == "SELECT VERSION()":
            return ScalarResult(self.version)
        if "information_schema.ENGINES" in statement:
            return ScalarResult(self.innodb_support)
        if "information_schema.COLLATIONS" in statement:
            return ScalarResult(self.collation)
        if "information_schema.TABLES" in statement:
            return ScalarResult(self.incompatible_tables)
        raise AssertionError(f"unexpected SQL: {statement}")


def test_mariadb_5_5_68_is_supported() -> None:
    connection = FakeConnection()

    info = validate_database_server(connection)

    assert info is not None
    assert info.family == "mariadb"
    assert info.version == (5, 5, 68)
    assert len(connection.statements) == 4


def test_mariadb_compatibility_prefix_uses_actual_version() -> None:
    info = validate_database_server(FakeConnection(version="5.5.5-10.11.6-MariaDB"))

    assert info is not None
    assert info.version == (10, 11, 6)


@pytest.mark.parametrize(
    "connection, message",
    [
        (FakeConnection(version="5.5.67-MariaDB"), "MariaDB >= 5.5.68"),
        (FakeConnection(innodb_support="NO"), "InnoDB"),
        (FakeConnection(collation=None), "utf8mb4_unicode_ci"),
        (
            FakeConnection(incompatible_tables="t_users:MyISAM"),
            "t_users:MyISAM",
        ),
    ],
)
def test_incompatible_server_is_rejected(
    connection: FakeConnection, message: str
) -> None:
    with pytest.raises(DatabaseCompatibilityError, match=message):
        validate_database_server(connection)


def test_mysql_8_is_still_supported() -> None:
    info = validate_database_server(FakeConnection(version="8.0.43"))

    assert info is not None
    assert info.family == "mysql"
    assert info.version == (8, 0, 43)


def test_sqlite_does_not_run_mysql_capability_queries() -> None:
    connection = FakeConnection(dialect="sqlite")

    assert validate_database_server(connection) is None
    assert connection.statements == []
