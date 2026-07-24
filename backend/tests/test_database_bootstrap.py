from __future__ import annotations

import typing

from app.core import database as database_module


class FakeResult:
    def __init__(self, value: typing.Optional[str]) -> None:
        self.value = value

    def scalar_one_or_none(self) -> typing.Optional[str]:
        return self.value


class FakeConnection:
    def __init__(self, existing_database: typing.Optional[str]) -> None:
        self.existing_database = existing_database
        self.parameters: typing.Optional[typing.Dict[str, str]] = None
        self.statements: typing.List[str] = []

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_args: typing.Any) -> None:
        return None

    def execute(self, _statement: typing.Any, parameters: typing.Dict[str, str]) -> FakeResult:
        self.parameters = parameters
        return FakeResult(self.existing_database)

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)


class FakeIdentifierPreparer:
    def quote(self, value: str) -> str:
        return f"`{value}`"


class FakeDialect:
    identifier_preparer = FakeIdentifierPreparer()


class FakeEngine:
    dialect = FakeDialect()

    def __init__(self, existing_database: typing.Optional[str]) -> None:
        self.connection = FakeConnection(existing_database)
        self.disposed = False

    def connect(self) -> FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_non_mysql_database_does_not_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(
        database_module,
        "create_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected engine")),
    )

    assert database_module.ensure_database_exists("sqlite:///./data/test.sqlite3") is False


def test_existing_mysql_database_is_not_created(monkeypatch) -> None:
    fake_engine = FakeEngine("openslt")
    captured: typing.Dict[str, typing.Any] = {}

    def fake_create_engine(url, **options):
        captured["url"] = url
        captured["options"] = options
        return fake_engine

    monkeypatch.setattr(database_module, "create_engine", fake_create_engine)

    assert database_module.ensure_database_exists(
        "mysql+pymysql://openslt:secret@127.0.0.1:3306/openslt?charset=utf8mb4"
    ) is False
    assert captured["url"].database is None
    assert captured["options"]["isolation_level"] == "AUTOCOMMIT"
    assert fake_engine.connection.parameters == {"database_name": "openslt"}
    assert fake_engine.connection.statements == []
    assert fake_engine.disposed


def test_missing_mysql_database_is_created(monkeypatch) -> None:
    fake_engine = FakeEngine(None)
    monkeypatch.setattr(database_module, "create_engine", lambda *_args, **_kwargs: fake_engine)

    assert database_module.ensure_database_exists(
        "mysql+pymysql://openslt:secret@127.0.0.1:3306/openslt?charset=utf8mb4"
    ) is True
    assert fake_engine.connection.statements == [
        "CREATE DATABASE IF NOT EXISTS `openslt` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    ]
    assert fake_engine.disposed
