from __future__ import annotations

import typing

from app.adapters.database import DatabaseOperationError, mysql_adapter, validate_database
from app.core.compat import to_thread
from app.models import Resource


TABLE_NAME = "t_global_settings"
KEY_COLUMN_CANDIDATES = ("setting_key", "setting_name", "name", "key", "param_name")
VALUE_COLUMN_CANDIDATES = ("setting_value", "value", "param_value")
DESCRIPTION_COLUMN_CANDIDATES = ("description",)


def _detected_column(
    columns: typing.Sequence[str],
    candidates: typing.Sequence[str],
    *,
    required: bool,
    label: str,
) -> typing.Optional[str]:
    folded = {column.casefold(): column for column in columns}
    matches = [folded[candidate] for candidate in candidates if candidate in folded]
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    raise DatabaseOperationError(
        "GLOBAL_SETTINGS_SCHEMA_UNKNOWN",
        f"无法唯一识别 {TABLE_NAME} 的{label}字段",
        409,
    )


def detect_setting_columns(
    columns: typing.Sequence[str],
) -> tuple[str, str, typing.Optional[str]]:
    key_column = typing.cast(
        str,
        _detected_column(columns, KEY_COLUMN_CANDIDATES, required=True, label="配置键"),
    )
    value_column = typing.cast(
        str,
        _detected_column(columns, VALUE_COLUMN_CANDIDATES, required=True, label="配置值"),
    )
    if key_column == value_column:
        raise DatabaseOperationError(
            "GLOBAL_SETTINGS_SCHEMA_UNKNOWN",
            f"{TABLE_NAME} 的配置键和值字段不能相同",
            409,
        )
    description_column = _detected_column(
        columns,
        DESCRIPTION_COLUMN_CANDIDATES,
        required=False,
        label="描述",
    )
    return key_column, value_column, description_column


def quote_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def read_setting_catalog(connection: typing.Any) -> list[dict[str, typing.Optional[str]]]:
    with connection.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(TABLE_NAME)}")
        columns = [str(row[0]) for row in cursor.fetchall()]
        key_column, _, description_column = detect_setting_columns(columns)
        description_sql = (
            quote_identifier(description_column) if description_column else "NULL"
        )
        cursor.execute(
            f"SELECT {quote_identifier(key_column)}, {description_sql} "
            f"FROM {quote_identifier(TABLE_NAME)} ORDER BY {quote_identifier(key_column)}"
        )
        rows = cursor.fetchall()

    items: list[dict[str, typing.Optional[str]]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row[0] or "").strip()
        if not key:
            continue
        folded_key = key.casefold()
        if folded_key in seen:
            raise DatabaseOperationError(
                "GLOBAL_SETTINGS_KEYS_AMBIGUOUS",
                f"{TABLE_NAME} 中存在重复配置键：{key}",
                409,
            )
        seen.add(folded_key)
        description = str(row[1]).strip() if row[1] is not None else ""
        items.append({"key": key, "description": description or None})
    return items


async def list_database_config_items(
    resource: Resource,
    database_name: str,
) -> list[dict[str, typing.Optional[str]]]:
    database_name = validate_database(resource, database_name)
    try:
        async with mysql_adapter.connection(resource, database_name) as connection:
            return await to_thread(read_setting_catalog, connection)
    except DatabaseOperationError:
        raise
    except Exception as exc:
        raise DatabaseOperationError(
            "GLOBAL_SETTINGS_READ_FAILED",
            f"读取 {database_name}.{TABLE_NAME} 配置项失败: {exc}",
            502,
        ) from exc
