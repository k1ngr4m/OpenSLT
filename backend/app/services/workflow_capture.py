from __future__ import annotations

import typing
from contextlib import suppress

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.database import mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.security import decrypt_secret
from app.core.time import beijing_now
from app.models import ConfigurationCaptureItem, ConfigurationCaptureSnapshot, Resource, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestScenario
from app.services.workflow_core import (
    FIELD_LABELS,
    SERVER_COMMANDS,
    WorkflowError,
    resource_map,
)
from app.services.database_config_catalog import TABLE_NAME, detect_setting_columns, quote_identifier
from app.workflow_node_configs import DatabaseConfig, ServerConfig, parse_node_config


DatabaseCaptureValue = typing.Tuple[typing.Any, typing.Optional[str]]


def _read_database_values(
    connection: typing.Any,
    keys: list[str],
) -> tuple[dict[str, DatabaseCaptureValue], str, str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(TABLE_NAME)}")
        key_column, value_column, description_column = detect_setting_columns(
            [str(row[0]) for row in cursor.fetchall()]
        )
        description_sql = (
            quote_identifier(description_column) if description_column else "NULL"
        )
        placeholders = ",".join(["%s"] * len(keys))
        sql = (
            f"SELECT {quote_identifier(key_column)}, {quote_identifier(value_column)}, "
            f"{description_sql} "
            f"FROM {quote_identifier(TABLE_NAME)} "
            f"WHERE {quote_identifier(key_column)} IN ({placeholders})"
        )
        cursor.execute(sql, keys)
        values = {
            str(row[0]): (
                row[1],
                (str(row[2]).strip() or None) if row[2] is not None else None,
            )
            for row in cursor.fetchall()
        }
    return values, key_column, value_column


def _ssh_options(resource: Resource) -> dict:
    options: dict[str, typing.Any] = {
        "host": resource.host, "port": resource.ssh_port, "username": resource.username,
        "known_hosts": None, "connect_timeout": 15,
    }
    password = decrypt_secret(resource.encrypted_password)
    private_key = decrypt_secret(resource.encrypted_private_key)
    if password:
        options["password"] = password
    if private_key:
        options["client_keys"] = [asyncssh.import_private_key(private_key)]
    return options


async def capture_server(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    *,
    scope: str,
    actor_id: typing.Optional[int],
    run_id: typing.Optional[int] = None,
    run_step_id: typing.Optional[int] = None,
    run_resources: typing.Optional[dict[str, Resource]] = None,
) -> list[ConfigurationCaptureSnapshot]:
    resources = run_resources or resource_map(db, version)
    config = typing.cast(ServerConfig, parse_node_config(node.node_type, node.config or {}))
    snapshots = []
    for target in config.targets:
        resource = resources.get(target.resource_type)
        if not resource:
            raise WorkflowError("WORKFLOW_RESOURCE_MISSING", "运行资源与节点配置不匹配", 409)
        attempt = (db.scalar(
            select(func.count(ConfigurationCaptureSnapshot.id)).where(
                ConfigurationCaptureSnapshot.workflow_node_id == node.id,
                ConfigurationCaptureSnapshot.scope == scope,
                ConfigurationCaptureSnapshot.resource_id == resource.id,
                ConfigurationCaptureSnapshot.run_id == run_id if run_id is not None
                else ConfigurationCaptureSnapshot.run_id.is_(None),
            )
        ) or 0) + 1
        snapshot = ConfigurationCaptureSnapshot(
            scenario_id=scenario.id, workflow_version_id=version.id, workflow_node_id=node.id,
            run_id=run_id, run_step_id=run_step_id, scope=scope, source_type="server",
            resource_id=resource.id, status="running", attempt=attempt, created_by=actor_id,
        )
        db.add(snapshot)
        db.flush()
        # Do not hold the platform database write lock while waiting on SSH I/O.
        # The running snapshot is intentionally durable so interrupted captures remain visible.
        db.commit()
        failed = False
        connection = None
        try:
            connection = await asyncssh.connect(**_ssh_options(resource))
            for field in target.fields:
                command = SERVER_COMMANDS[field]
                try:
                    result = await connection.run(command, check=False)
                    raw, exit_code = (result.stdout or result.stderr).strip(), result.exit_status
                    value = result.stdout.strip()
                    if exit_code != 0 or not value:
                        raise RuntimeError(result.stderr.strip() or "命令没有返回结果")
                    snapshot.items.append(ConfigurationCaptureItem(
                        item_key=field, item_label=FIELD_LABELS[field], value_text=value,
                        source_reference=command, raw_output=raw[:65535], exit_code=exit_code, status="succeeded",
                    ))
                except Exception as exc:
                    failed = True
                    snapshot.items.append(ConfigurationCaptureItem(
                        item_key=field, item_label=FIELD_LABELS[field], source_reference=command,
                        raw_output="", status="failed", error_message=str(exc),
                    ))
        except Exception as exc:
            failed = True
            snapshot.error_message = str(exc)
        finally:
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()
        snapshot.status = "failed" if failed else "succeeded"
        snapshot.finished_at = beijing_now()
        db.commit()
        snapshots.append(snapshot)
    return snapshots


async def capture_database(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    *,
    scope: str,
    actor_id: typing.Optional[int],
    run_id: typing.Optional[int] = None,
    run_step_id: typing.Optional[int] = None,
    run_resources: typing.Optional[dict[str, Resource]] = None,
) -> list[ConfigurationCaptureSnapshot]:
    resources = run_resources or resource_map(db, version)
    config = typing.cast(DatabaseConfig, parse_node_config(node.node_type, node.config or {}))
    resource = resources.get("database")
    if not resource:
        raise WorkflowError("WORKFLOW_RESOURCE_MISSING", "运行资源缺少数据库", 409)
    database_name = validate_database(resource, config.database_name)
    keys = list(dict.fromkeys(config.keys))
    attempt = (db.scalar(
        select(func.count(ConfigurationCaptureSnapshot.id)).where(
            ConfigurationCaptureSnapshot.workflow_node_id == node.id,
            ConfigurationCaptureSnapshot.scope == scope,
            ConfigurationCaptureSnapshot.resource_id == resource.id,
            ConfigurationCaptureSnapshot.run_id == run_id if run_id is not None
            else ConfigurationCaptureSnapshot.run_id.is_(None),
        )
    ) or 0) + 1
    snapshot = ConfigurationCaptureSnapshot(
        scenario_id=scenario.id, workflow_version_id=version.id, workflow_node_id=node.id,
        run_id=run_id, run_step_id=run_step_id, scope=scope, source_type="database",
        resource_id=resource.id, database_name=database_name, status="running",
        attempt=attempt, created_by=actor_id,
    )
    db.add(snapshot)
    db.flush()
    # Remote database reads may take seconds. Release the platform database write lock first
    # so concurrent catalog and audit requests can complete while this capture is running.
    db.commit()
    try:
        async with mysql_adapter.connection(resource, database_name) as connection:
            def query() -> tuple[dict[str, tuple[typing.Any, typing.Optional[str]]], str]:
                values, key_column, value_column = _read_database_values(connection, keys)
                return values, f"{database_name}.t_global_settings.{key_column}/{value_column}"
            values, source = await to_thread(query)
        failed = False
        for key in keys:
            if key in values:
                value, description = values[key]
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, item_description=description,
                    value_text=str(value), source_reference=source,
                    raw_output=str(value), exit_code=0, status="succeeded",
                ))
            else:
                failed = True
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, item_description=None,
                    source_reference=source, raw_output="",
                    status="failed", error_message="配置项不存在",
                ))
        snapshot.status = "failed" if failed else "succeeded"
    except Exception as exc:
        snapshot.status = "failed"
        snapshot.error_message = str(exc)
    snapshot.finished_at = beijing_now()
    db.commit()
    return [snapshot]


async def preview_node(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion, node: ScenarioWorkflowNode, actor_id: int) -> list[ConfigurationCaptureSnapshot]:
    if node.node_type == "server_config":
        return await capture_server(db, scenario, version, node, scope="preview", actor_id=actor_id)
    if node.node_type == "database_config":
        return await capture_database(db, scenario, version, node, scope="preview", actor_id=actor_id)
    raise WorkflowError("NODE_PREVIEW_NOT_SUPPORTED", "该节点不支持预采集", 409)
