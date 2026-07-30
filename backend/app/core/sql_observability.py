from __future__ import annotations

import hashlib
import re
import time
import typing

import pymysql
import sqlglot
from pymysql.cursors import Cursor, SSCursor
from sqlalchemy import event
from sqlglot import exp

from app.core.config import settings
from app.core.logging import bounded_json, redact, sql_logging_suppressed_ctx
from app.core.observability import emit_observability_event

_STRING_LITERAL = re.compile(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"")
_NUMBER_LITERAL = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])")
_WHITESPACE = re.compile(r"\s+")


def sql_template(statement: typing.Any) -> str:
    text = statement.decode("utf-8", errors="replace") if isinstance(statement, bytes) else str(statement)
    try:
        expressions = sqlglot.parse(text, read="mysql")
        rendered: typing.List[str] = []
        for expression in expressions:
            for literal in list(expression.find_all(exp.Literal)):
                literal.replace(exp.Placeholder())
            rendered.append(expression.sql(dialect="mysql"))
        text = typing.cast(str, redact("; ".join(rendered)))
    except (sqlglot.errors.ParseError, AttributeError, TypeError, ValueError):
        text = typing.cast(str, redact(text))
        text = _STRING_LITERAL.sub("?", text)
        text = _NUMBER_LITERAL.sub("?", text)
    encoded = text.encode("utf-8")
    if len(encoded) > settings.observability_sql_limit_bytes:
        text = encoded[: settings.observability_sql_limit_bytes].decode("utf-8", errors="ignore") + "…"
    return text


def sql_fingerprint(template: str) -> str:
    normalized = _WHITESPACE.sub(" ", template).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _operation(template: str) -> str:
    match = re.search(r"[A-Za-z]+", template)
    return match.group(0).upper() if match else "SQL"


def _parameters(
    parameters: typing.Any,
    context: typing.Any = None,
    executemany: bool = False,
) -> typing.Any:
    value = parameters
    position_names = getattr(getattr(context, "compiled", None), "positiontup", None)
    if position_names and isinstance(parameters, (list, tuple)) and not executemany:
        value = dict(zip(position_names, parameters))
    elif executemany and isinstance(parameters, (list, tuple)):
        value = {"batch_size": len(parameters), "sample": list(parameters[:3])}
    safe, truncated = bounded_json(value, settings.observability_sql_params_limit_bytes)
    return {"value": safe, "truncated": truncated}


def _emit_sql(
    *,
    statement: typing.Any,
    parameters: typing.Any = None,
    context: typing.Any = None,
    executemany: bool = False,
    duration_ms: int = 0,
    rowcount: typing.Optional[int] = None,
    result: str = "success",
    error: typing.Optional[BaseException] = None,
    database_scope: str,
    database: typing.Optional[str] = None,
    resource_id: typing.Optional[int] = None,
    source: str,
) -> None:
    if sql_logging_suppressed_ctx.get():
        return
    template = sql_template(statement)
    event_payload: typing.Dict[str, typing.Any] = {
        "category": "sql",
        "log_type": "sql",
        "event": "sql_execute",
        "level": "ERROR" if error else "INFO",
        "source": source,
        "duration_ms": duration_ms,
        "result": result,
        "database_scope": database_scope,
        "database": database,
        "resource_id": resource_id,
        "operation": _operation(template),
        "statement_template": template,
        "parameters": _parameters(parameters, context, executemany),
        "executemany": executemany,
        "rowcount": rowcount,
        "sql_fingerprint": sql_fingerprint(template),
    }
    if error is not None:
        event_payload["error_type"] = type(error).__name__
        event_payload["error_message"] = typing.cast(str, redact(str(error)))[:2048]
    emit_observability_event(event_payload)


def register_sqlalchemy_observability(engine: typing.Any) -> None:
    if getattr(engine, "_openslt_observability_registered", False):
        return
    engine._openslt_observability_registered = True

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        conn: typing.Any,
        cursor: typing.Any,
        statement: str,
        parameters: typing.Any,
        context: typing.Any,
        executemany: bool,
    ) -> None:
        context._openslt_started_at = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(
        conn: typing.Any,
        cursor: typing.Any,
        statement: str,
        parameters: typing.Any,
        context: typing.Any,
        executemany: bool,
    ) -> None:
        started = getattr(context, "_openslt_started_at", time.perf_counter())
        _emit_sql(
            statement=statement,
            parameters=parameters,
            context=context,
            executemany=executemany,
            duration_ms=round((time.perf_counter() - started) * 1000),
            rowcount=getattr(cursor, "rowcount", None),
            database_scope="platform",
            database=conn.engine.url.database,
            source="sqlalchemy",
        )

    @event.listens_for(engine, "handle_error")
    def handle_error(exception_context: typing.Any) -> None:
        execution_context = exception_context.execution_context
        started = getattr(execution_context, "_openslt_started_at", time.perf_counter())
        _emit_sql(
            statement=exception_context.statement or "SQL",
            parameters=exception_context.parameters,
            context=execution_context,
            duration_ms=round((time.perf_counter() - started) * 1000),
            result="failed",
            error=exception_context.original_exception,
            database_scope="platform",
            database=exception_context.engine.url.database,
            source="sqlalchemy",
        )

    for transaction_event in ("begin", "commit", "rollback"):
        def transaction_listener(conn: typing.Any, name: str = transaction_event) -> None:
            _emit_sql(
                statement=name.upper(),
                duration_ms=0,
                result="success",
                database_scope="platform",
                database=conn.engine.url.database,
                source="sqlalchemy",
            )

        event.listen(engine, transaction_event, transaction_listener)


class _LoggingCursorMixin:
    _openslt_executemany = False

    def execute(self, query: typing.Any, args: typing.Any = None) -> int:
        if self._openslt_executemany or sql_logging_suppressed_ctx.get():
            return typing.cast(int, super().execute(query, args))
        started = time.perf_counter()
        try:
            result = typing.cast(int, super().execute(query, args))
        except BaseException as exc:
            self._record(query, args, started, error=exc)
            raise
        self._record(query, args, started)
        return result

    def executemany(self, query: typing.Any, args: typing.Any) -> int:
        if sql_logging_suppressed_ctx.get():
            return typing.cast(int, super().executemany(query, args))
        started = time.perf_counter()
        self._openslt_executemany = True
        try:
            result = typing.cast(int, super().executemany(query, args))
        except BaseException as exc:
            self._record(query, args, started, executemany=True, error=exc)
            raise
        finally:
            self._openslt_executemany = False
        self._record(query, args, started, executemany=True)
        return result

    def _record(
        self,
        query: typing.Any,
        args: typing.Any,
        started: float,
        *,
        executemany: bool = False,
        error: typing.Optional[BaseException] = None,
    ) -> None:
        connection = self.connection
        _emit_sql(
            statement=query,
            parameters=args,
            executemany=executemany,
            duration_ms=round((time.perf_counter() - started) * 1000),
            rowcount=getattr(self, "rowcount", None),
            result="failed" if error else "success",
            error=error,
            database_scope="resource",
            database=getattr(connection, "_openslt_database", None),
            resource_id=getattr(connection, "_openslt_resource_id", None),
            source="pymysql",
        )


class LoggingCursor(_LoggingCursorMixin, Cursor):
    pass


class LoggingSSCursor(_LoggingCursorMixin, SSCursor):
    pass


class LoggingConnection(pymysql.connections.Connection):
    _openslt_resource_id: typing.Optional[int] = None
    _openslt_database: typing.Optional[str] = None

    def cursor(self, cursor: typing.Any = None) -> typing.Any:
        cursor_class = cursor
        if cursor_class is None or cursor_class is Cursor:
            cursor_class = LoggingCursor
        elif cursor_class is SSCursor:
            cursor_class = LoggingSSCursor
        return super().cursor(cursor_class)

    def commit(self) -> None:
        started = time.perf_counter()
        try:
            super().commit()
        except BaseException as exc:
            self._record_transaction("COMMIT", started, exc)
            raise
        self._record_transaction("COMMIT", started)

    def rollback(self) -> None:
        started = time.perf_counter()
        try:
            super().rollback()
        except BaseException as exc:
            self._record_transaction("ROLLBACK", started, exc)
            raise
        self._record_transaction("ROLLBACK", started)

    def _record_transaction(
        self, operation: str, started: float, error: typing.Optional[BaseException] = None
    ) -> None:
        _emit_sql(
            statement=operation,
            duration_ms=round((time.perf_counter() - started) * 1000),
            result="failed" if error else "success",
            error=error,
            database_scope="resource",
            database=self._openslt_database,
            resource_id=self._openslt_resource_id,
            source="pymysql",
        )


class LoggingCursorProxy:
    def __init__(self, cursor: typing.Any, connection: "LoggingConnectionProxy") -> None:
        self._cursor = cursor
        self.connection = connection

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self._cursor, name)

    def __enter__(self) -> "LoggingCursorProxy":
        self._cursor.__enter__()
        return self

    def __exit__(self, *args: typing.Any) -> typing.Any:
        return self._cursor.__exit__(*args)

    def execute(self, query: typing.Any, args: typing.Any = None) -> int:
        started = time.perf_counter()
        try:
            result = (
                self._cursor.execute(query)
                if args is None
                else self._cursor.execute(query, args)
            )
        except BaseException as exc:
            self._record(query, args, started, error=exc)
            raise
        self._record(query, args, started)
        return typing.cast(int, result)

    def executemany(self, query: typing.Any, args: typing.Any) -> int:
        started = time.perf_counter()
        try:
            result = self._cursor.executemany(query, args)
        except BaseException as exc:
            self._record(query, args, started, executemany=True, error=exc)
            raise
        self._record(query, args, started, executemany=True)
        return typing.cast(int, result)

    def _record(
        self,
        query: typing.Any,
        args: typing.Any,
        started: float,
        *,
        executemany: bool = False,
        error: typing.Optional[BaseException] = None,
    ) -> None:
        _emit_sql(
            statement=query,
            parameters=args,
            executemany=executemany,
            duration_ms=round((time.perf_counter() - started) * 1000),
            rowcount=getattr(self._cursor, "rowcount", None),
            result="failed" if error else "success",
            error=error,
            database_scope="resource",
            database=self.connection._openslt_database,
            resource_id=self.connection._openslt_resource_id,
            source="pymysql",
        )


class LoggingConnectionProxy:
    def __init__(
        self,
        connection: typing.Any,
        resource_id: typing.Optional[int],
        database: typing.Optional[str],
    ) -> None:
        self._connection = connection
        self._openslt_resource_id = resource_id
        self._openslt_database = database

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self._connection, name)

    def cursor(self, cursor: typing.Any = None) -> LoggingCursorProxy:
        raw_cursor = self._connection.cursor(cursor) if cursor is not None else self._connection.cursor()
        return LoggingCursorProxy(raw_cursor, self)

    def commit(self) -> None:
        self._transaction("COMMIT", self._connection.commit)

    def rollback(self) -> None:
        self._transaction("ROLLBACK", self._connection.rollback)

    def close(self) -> None:
        self._connection.close()

    def _transaction(self, operation: str, callback: typing.Callable[[], typing.Any]) -> None:
        started = time.perf_counter()
        try:
            callback()
        except BaseException as exc:
            self._record_transaction(operation, started, exc)
            raise
        self._record_transaction(operation, started)

    def _record_transaction(
        self, operation: str, started: float, error: typing.Optional[BaseException] = None
    ) -> None:
        _emit_sql(
            statement=operation,
            duration_ms=round((time.perf_counter() - started) * 1000),
            result="failed" if error else "success",
            error=error,
            database_scope="resource",
            database=self._openslt_database,
            resource_id=self._openslt_resource_id,
            source="pymysql",
        )


def connect_resource_database(
    *,
    resource_id: typing.Optional[int] = None,
    observability_database: typing.Optional[str] = None,
    **kwargs: typing.Any
) -> LoggingConnectionProxy:
    connection = pymysql.connect(**kwargs)
    return LoggingConnectionProxy(
        connection,
        resource_id,
        observability_database or kwargs.get("database"),
    )
