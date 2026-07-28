from __future__ import annotations

import json
import typing
from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeEngine
from sqlalchemy.types import TypeDecorator

from app.core.time import as_beijing, as_storage_utc


class BeijingDateTime(TypeDecorator):
    """Persist UTC while exposing every ORM timestamp as aware Beijing time."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[typing.Any]:
        # SQLite and MySQL both round-trip DATETIME values without their offset.
        # Keeping the physical representation naive also preserves compatibility
        # with every timestamp already stored by OpenSLT.
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(
        self, value: typing.Optional[datetime], dialect: Dialect
    ) -> typing.Optional[datetime]:
        del dialect
        if value is None:
            return None
        return as_storage_utc(value)

    def process_result_value(
        self, value: typing.Optional[datetime], dialect: Dialect
    ) -> typing.Optional[datetime]:
        del dialect
        if value is None:
            return None
        return as_beijing(value)


class JSONText(TypeDecorator):
    """Store JSON values as text for compatibility with older MariaDB releases."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[typing.Any]:
        if dialect.name == "mysql":
            return dialect.type_descriptor(LONGTEXT())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: typing.Any, dialect: Dialect) -> typing.Optional[str]:
        del dialect
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value: typing.Any, dialect: Dialect) -> typing.Any:
        del dialect
        if value is None or isinstance(value, (dict, list, int, float, bool)):
            return value
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return json.loads(value)
