from __future__ import annotations

import typing

import asyncio
import csv
import hashlib
import io
import os
import ssl
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as time_value
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator

import asyncssh
import pymysql
import sqlglot
from openpyxl import Workbook
from pymysql.cursors import SSCursor
from sqlglot import exp

from app.core.compat import to_thread
from app.core.security import decrypt_secret
from app.models import Resource

QUERY_PREVIEW_LIMIT = 500
CSV_EXPORT_LIMIT = 1_000_000
XLSX_EXPORT_LIMIT = 100_000
UPDATE_LIMIT = 1_000
OPERATION_TIMEOUT_SECONDS = 300
SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}


class DatabaseOperationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass
class SqlPlan:
    sql: str
    normalized_sql: str
    fingerprint: str
    statement: exp.Expression
    table_name: typing.Union[str, None] = None
    count_sql: typing.Union[str, None] = None


@dataclass
class DatabaseDiscoveryConfig:
    database_host: str
    database_port: int
    database_username: str
    database_password: typing.Union[str, None]
    database_tls_enabled: bool
    connection_mode: str
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_password: typing.Union[str, None] = None
    ssh_private_key: typing.Union[str, None] = None


def filter_database_names(rows: typing.List[Any]) -> typing.Tuple[typing.List[str], int]:
    names: typing.Dict[str, str] = {}
    filtered_system_count = 0
    for row in rows:
        value = row[0] if isinstance(row, (tuple, list)) else row
        name = str(value).strip()
        if not name:
            continue
        key = name.casefold()
        if key in SYSTEM_DATABASES:
            filtered_system_count += 1
            continue
        names.setdefault(key, name)
    return sorted(names.values(), key=str.casefold), filtered_system_count


def _single_statement(sql: str) -> exp.Expression:
    try:
        statements = [item for item in sqlglot.parse(sql, read="mysql") if item is not None]
    except sqlglot.errors.ParseError as exc:
        raise DatabaseOperationError("INVALID_SQL", f"SQL 解析失败: {exc}") from exc
    if len(statements) != 1:
        raise DatabaseOperationError("SINGLE_STATEMENT_REQUIRED", "每次只能执行一条 SQL")
    return statements[0]


def _validate_database_references(statement: exp.Expression, database_name: str) -> None:
    for table in statement.find_all(exp.Table):
        if table.db and table.db.casefold() != database_name.casefold():
            raise DatabaseOperationError(
                "DATABASE_NOT_ALLOWED",
                f"SQL 只能访问当前选择的数据库 {database_name}",
            )


def parse_select(sql: str, database_name: str) -> SqlPlan:
    statement = _single_statement(sql)
    if not isinstance(statement, exp.Select):
        raise DatabaseOperationError("SELECT_REQUIRED", "此操作只允许 SELECT")
    if statement.args.get("into") or statement.find(exp.Lock):
        raise DatabaseOperationError("UNSAFE_SELECT", "SELECT INTO 和加锁查询不受支持")
    _validate_database_references(statement, database_name)
    normalized = statement.sql(dialect="mysql", pretty=False)
    return SqlPlan(
        sql=sql.strip().rstrip(";"),
        normalized_sql=normalized,
        fingerprint=hashlib.sha256(normalized.encode()).hexdigest(),
        statement=statement,
    )


def parse_update(sql: str, database_name: str) -> SqlPlan:
    statement = _single_statement(sql)
    if not isinstance(statement, exp.Update):
        raise DatabaseOperationError("UPDATE_REQUIRED", "此操作只允许 UPDATE")
    if not statement.args.get("where"):
        raise DatabaseOperationError("UPDATE_WHERE_REQUIRED", "UPDATE 必须包含 WHERE 条件")
    forbidden = (exp.Join, exp.Subquery, exp.Select, exp.Order, exp.Limit)
    if any(statement.find(node_type) for node_type in forbidden):
        raise DatabaseOperationError(
            "COMPLEX_UPDATE_NOT_ALLOWED",
            "UPDATE 不支持 JOIN、子查询、ORDER 或 LIMIT",
        )
    target = statement.this
    if not isinstance(target, exp.Table):
        raise DatabaseOperationError("SINGLE_TABLE_UPDATE_REQUIRED", "UPDATE 只能修改单个表")
    _validate_database_references(statement, database_name)
    target_name = target.name
    database_prefix = f"`{database_name.replace('`', '``')}`."
    table_sql = target.sql(dialect="mysql")
    if not target.db:
        table_sql = database_prefix + table_sql
    where_sql = statement.args["where"].sql(dialect="mysql")
    count_sql = f"SELECT 1 FROM {table_sql} {where_sql} LIMIT {UPDATE_LIMIT + 1}"
    normalized = statement.sql(dialect="mysql", pretty=False)
    return SqlPlan(
        sql=sql.strip().rstrip(";"),
        normalized_sql=normalized,
        fingerprint=hashlib.sha256(normalized.encode()).hexdigest(),
        statement=statement,
        table_name=target_name,
        count_sql=count_sql,
    )


def validate_database(resource: Resource, database_name: str) -> str:
    if resource.resource_type != "database":
        raise DatabaseOperationError("DATABASE_RESOURCE_REQUIRED", "该资源不是数据库资源", 409)
    if not resource.is_enabled:
        raise DatabaseOperationError("RESOURCE_DISABLED", "数据库资源已停用", 409)
    configured = resource.database_names or []
    match = next((name for name in configured if name.casefold() == database_name.strip().casefold()), None)
    if not match:
        raise DatabaseOperationError("DATABASE_NOT_CONFIGURED", "所选数据库不在资源配置中", 400)
    return match


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time_value)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _mock_columns(plan: SqlPlan) -> typing.List[str]:
    columns: typing.List[str] = []
    for index, expression in enumerate(plan.statement.expressions, 1):
        if isinstance(expression, exp.Star):
            return ["mock_id", "mock_value", "mock_updated_at"]
        columns.append(expression.alias_or_name or f"column_{index}")
    return columns or ["result"]


def simulated_select(plan: SqlPlan, row_limit: int = 8) -> typing.Dict[str, Any]:
    columns = _mock_columns(plan)
    seed = int(plan.fingerprint[:8], 16)
    rows: typing.List[typing.Dict[str, Any]] = []
    for row_index in range(1, row_limit + 1):
        row: typing.Dict[str, Any] = {}
        for column_index, column in enumerate(columns):
            lowered = column.casefold()
            if lowered == "mock_id" or lowered.endswith("_id") or lowered == "id":
                value: Any = row_index
            elif "time" in lowered or "date" in lowered:
                value = f"2026-01-{row_index:02d}T09:{(seed + column_index) % 60:02d}:00"
            else:
                value = f"{column}_{(seed + row_index + column_index) % 97}"
            row[column] = value
        rows.append(row)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": False,
        "elapsed_ms": 0,
        "simulated": True,
    }


def simulated_update_rows(plan: SqlPlan) -> int:
    return int(plan.fingerprint[:8], 16) % 5 + 1


class MySQLAdapter:
    async def discover_databases(self, config: DatabaseDiscoveryConfig) -> typing.Tuple[typing.List[str], int]:
        ssh_connection = None
        tunnel = None
        connection = None
        host = config.database_host
        port = config.database_port
        try:
            if config.connection_mode == "ssh_tunnel":
                options: typing.Dict[str, Any] = {
                    "host": config.ssh_host,
                    "port": config.ssh_port,
                    "username": config.ssh_username,
                    "known_hosts": None,
                    "connect_timeout": 15,
                }
                if config.ssh_password:
                    options["password"] = config.ssh_password
                if config.ssh_private_key:
                    options["client_keys"] = [asyncssh.import_private_key(config.ssh_private_key)]
                ssh_connection = await asyncssh.connect(**options)
                tunnel = await ssh_connection.forward_local_port("127.0.0.1", 0, host, port)
                host = "127.0.0.1"
                port = tunnel.get_port()

            ssl_context = None
            if config.database_tls_enabled:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            connection = await to_thread(
                pymysql.connect,
                host=host,
                port=port,
                user=config.database_username,
                password=config.database_password or "",
                database=None,
                charset="utf8mb4",
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
                autocommit=True,
                ssl=ssl_context,
            )

            def discover() -> typing.List[Any]:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW DATABASES")
                    return list(cursor.fetchall())

            rows = await to_thread(discover)
            return filter_database_names(rows)
        except DatabaseOperationError:
            raise
        except Exception as exc:
            raise DatabaseOperationError(
                "DATABASE_DISCOVERY_FAILED",
                f"读取数据库名称失败: {exc}",
                502,
            ) from exc
        finally:
            if connection:
                await to_thread(connection.close)
            if tunnel:
                tunnel.close()
                await tunnel.wait_closed()
            if ssh_connection:
                ssh_connection.close()
                await ssh_connection.wait_closed()

    @asynccontextmanager
    async def connection(self, resource: Resource, database_name: str):
        ssh_connection = None
        tunnel = None
        host = resource.database_host or ""
        port = resource.database_port or 3306
        try:
            if resource.database_connection_mode == "ssh_tunnel":
                options: typing.Dict[str, Any] = {
                    "host": resource.host,
                    "port": resource.ssh_port,
                    "username": resource.username,
                    "known_hosts": None,
                }
                password = decrypt_secret(resource.encrypted_password)
                private_key = decrypt_secret(resource.encrypted_private_key)
                if password:
                    options["password"] = password
                if private_key:
                    options["client_keys"] = [asyncssh.import_private_key(private_key)]
                ssh_connection = await asyncssh.connect(**options)
                tunnel = await ssh_connection.forward_local_port("127.0.0.1", 0, host, port)
                host = "127.0.0.1"
                port = tunnel.get_port()

            ssl_context = None
            if resource.database_tls_enabled:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            connection = await to_thread(
                pymysql.connect,
                host=host,
                port=port,
                user=resource.database_username or "",
                password=decrypt_secret(resource.encrypted_database_password) or "",
                database=database_name,
                charset="utf8mb4",
                connect_timeout=10,
                read_timeout=OPERATION_TIMEOUT_SECONDS,
                write_timeout=OPERATION_TIMEOUT_SECONDS,
                autocommit=False,
                ssl=ssl_context,
            )
            try:
                yield connection
            finally:
                await to_thread(connection.close)
        finally:
            if tunnel:
                tunnel.close()
                await tunnel.wait_closed()
            if ssh_connection:
                ssh_connection.close()
                await ssh_connection.wait_closed()

    async def health(self, resource: Resource) -> typing.Dict[str, Any]:
        details = []
        version = None
        for database_name in resource.database_names or []:
            try:
                async with self.connection(resource, database_name) as connection:
                    def check() -> typing.Tuple[Any, Any]:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT 1")
                            one = cursor.fetchone()
                            cursor.execute("SELECT VERSION()")
                            return one, cursor.fetchone()

                    one, version_row = await to_thread(check)
                    version = version_row[0] if version_row else None
                    details.append({"database": database_name, "ok": bool(one), "version": version})
            except Exception as exc:
                details.append({"database": database_name, "ok": False, "message": str(exc)})
        ok = bool(details) and all(item["ok"] for item in details)
        return {
            "ok": ok,
            "message": "数据库连接成功" if ok else "部分或全部数据库连接失败",
            "details": details,
            "version": version,
            "simulated": False,
        }

    async def select(self, resource: Resource, database_name: str, plan: SqlPlan) -> typing.Dict[str, Any]:
        started = time.perf_counter()
        async with self.connection(resource, database_name) as connection:
            def run() -> typing.Tuple[typing.List[str], typing.List[typing.Dict[str, Any]], bool]:
                with connection.cursor(SSCursor) as cursor:
                    cursor.execute(plan.sql)
                    columns = [item[0] for item in (cursor.description or [])]
                    values = cursor.fetchmany(QUERY_PREVIEW_LIMIT + 1)
                    truncated = len(values) > QUERY_PREVIEW_LIMIT
                    rows = [
                        {column: _json_value(value) for column, value in zip(columns, row)}
                        for row in values[:QUERY_PREVIEW_LIMIT]
                    ]
                    return columns, rows, truncated

            columns, rows, truncated = await to_thread(run)
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "simulated": False,
        }

    async def preview_update(self, resource: Resource, database_name: str, plan: SqlPlan) -> int:
        async with self.connection(resource, database_name) as connection:
            def count() -> int:
                with connection.cursor() as cursor:
                    cursor.execute(plan.count_sql)
                    return len(cursor.fetchall())

            return await to_thread(count)

    async def execute_update(
        self,
        resource: Resource,
        database_name: str,
        plan: SqlPlan,
        expected_rows: int,
    ) -> int:
        async with self.connection(resource, database_name) as connection:
            def execute() -> int:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(f"{plan.count_sql} FOR UPDATE")
                        current_rows = len(cursor.fetchall())
                        if current_rows > UPDATE_LIMIT:
                            raise DatabaseOperationError(
                                "UPDATE_LIMIT_EXCEEDED",
                                f"UPDATE 预计影响超过 {UPDATE_LIMIT} 行",
                                409,
                            )
                        if current_rows != expected_rows:
                            raise DatabaseOperationError(
                                "UPDATE_PREVIEW_CHANGED",
                                "目标数据在确认期间发生变化，请重新预览",
                                409,
                            )
                        affected = cursor.execute(plan.sql)
                        if affected > UPDATE_LIMIT:
                            raise DatabaseOperationError(
                                "UPDATE_LIMIT_EXCEEDED",
                                f"UPDATE 实际影响超过 {UPDATE_LIMIT} 行",
                                409,
                            )
                    connection.commit()
                    return affected
                except Exception:
                    connection.rollback()
                    raise

            return await to_thread(execute)

    async def iter_csv(
        self,
        resource: Resource,
        database_name: str,
        plan: SqlPlan,
    ) -> typing.AsyncIterator[bytes]:
        deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS
        async with self.connection(resource, database_name) as connection:
            cursor = connection.cursor(SSCursor)
            try:
                await to_thread(cursor.execute, plan.sql)
                columns = [item[0] for item in (cursor.description or [])]
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(columns)
                yield ("\ufeff" + output.getvalue()).encode("utf-8")
                exported = 0
                while True:
                    rows = await to_thread(cursor.fetchmany, 1_000)
                    if not rows:
                        break
                    exported += len(rows)
                    if exported > CSV_EXPORT_LIMIT:
                        raise DatabaseOperationError("EXPORT_LIMIT_EXCEEDED", "CSV 导出超过 100 万行", 409)
                    if time.monotonic() > deadline:
                        raise DatabaseOperationError("QUERY_TIMEOUT", "导出超过 5 分钟", 408)
                    output = io.StringIO()
                    writer = csv.writer(output)
                    writer.writerows([[_json_value(value) for value in row] for row in rows])
                    yield output.getvalue().encode("utf-8")
            finally:
                await to_thread(cursor.close)

    async def write_xlsx(self, resource: Resource, database_name: str, plan: SqlPlan) -> Path:
        handle, filename = tempfile.mkstemp(prefix="openslt-db-", suffix=".xlsx")
        os.close(handle)
        Path(filename).unlink(missing_ok=True)
        deadline = time.monotonic() + OPERATION_TIMEOUT_SECONDS
        try:
            async with self.connection(resource, database_name) as connection:
                cursor = connection.cursor(SSCursor)
                try:
                    await to_thread(cursor.execute, plan.sql)
                    columns = [item[0] for item in (cursor.description or [])]
                    workbook = Workbook(write_only=True)
                    worksheet = workbook.create_sheet("Query")
                    worksheet.append(columns)
                    exported = 0
                    while True:
                        rows = await to_thread(cursor.fetchmany, 1_000)
                        if not rows:
                            break
                        exported += len(rows)
                        if exported > XLSX_EXPORT_LIMIT:
                            raise DatabaseOperationError("EXPORT_LIMIT_EXCEEDED", "XLSX 导出超过 10 万行", 409)
                        if time.monotonic() > deadline:
                            raise DatabaseOperationError("QUERY_TIMEOUT", "导出超过 5 分钟", 408)
                        for row in rows:
                            worksheet.append([_json_value(value) for value in row])
                    await to_thread(workbook.save, filename)
                finally:
                    await to_thread(cursor.close)
            return Path(filename)
        except Exception:
            Path(filename).unlink(missing_ok=True)
            raise


mysql_adapter = MySQLAdapter()
