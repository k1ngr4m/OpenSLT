from __future__ import annotations

import json
import typing

from sqlalchemy import Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeEngine
from sqlalchemy.types import TypeDecorator


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
