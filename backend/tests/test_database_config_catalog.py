from __future__ import annotations

import pytest

from app.adapters.database import DatabaseOperationError
from app.services.database_config_catalog import detect_setting_columns, read_setting_catalog


class FakeCursor:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, *_params):
        self.sql = sql

    def fetchall(self):
        if self.sql.startswith("SHOW COLUMNS"):
            return [(column,) for column in self.columns]
        return self.rows


class FakeConnection:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.columns, self.rows)


def test_catalog_uses_discovered_key_value_and_optional_description_columns():
    connection = FakeConnection(
        ["module_id", "setting_key", "setting_value", "description"],
        [
            ("ACCOUNT_QUANTITY", ""),
            ("ASYNC_MKT_MSG_PROC", "市场回报的默认同步模式"),
        ],
    )

    assert read_setting_catalog(connection) == [
        {"key": "ACCOUNT_QUANTITY", "description": None},
        {"key": "ASYNC_MKT_MSG_PROC", "description": "市场回报的默认同步模式"},
    ]
    assert detect_setting_columns(["SETTING_KEY", "SETTING_VALUE"]) == (
        "SETTING_KEY",
        "SETTING_VALUE",
        None,
    )


def test_catalog_rejects_unknown_schema_and_duplicate_keys():
    with pytest.raises(DatabaseOperationError) as schema_error:
        detect_setting_columns(["id", "description"])
    assert schema_error.value.code == "GLOBAL_SETTINGS_SCHEMA_UNKNOWN"

    connection = FakeConnection(
        ["setting_key", "setting_value", "description"],
        [("DUPLICATE", "one"), ("duplicate", "two")],
    )
    with pytest.raises(DatabaseOperationError) as duplicate_error:
        read_setting_catalog(connection)
    assert duplicate_error.value.code == "GLOBAL_SETTINGS_KEYS_AMBIGUOUS"
