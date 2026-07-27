from __future__ import annotations

import typing
from contextlib import suppress
from datetime import datetime, timezone

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.database import mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.security import decrypt_secret
from app.models import ConfigurationCaptureItem, ConfigurationCaptureSnapshot, Resource, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestScenario
from app.services.workflow_core import (
    FIELD_LABELS,
    KEY_COLUMN_CANDIDATES,
    SERVER_COMMANDS,
    VALUE_COLUMN_CANDIDATES,
    WorkflowError,
    resource_map,
)
from app.workflow_node_configs import DatabaseConfig, ServerConfig, parse_node_config

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
        snapshot.finished_at = datetime.now(timezone.utc)
        snapshots.append(snapshot)
    db.flush()
    return snapshots


def _detect_setting_columns(columns: list[str]) -> tuple[str, str]:
    folded = {column.casefold(): column for column in columns}
    keys = [folded[item] for item in KEY_COLUMN_CANDIDATES if item in folded]
    values = [folded[item] for item in VALUE_COLUMN_CANDIDATES if item in folded]
    if len(keys) != 1 or len(values) != 1 or keys[0] == values[0]:
        raise WorkflowError("GLOBAL_SETTINGS_SCHEMA_UNKNOWN", "无法唯一识别 t_global_settings 的配置键和值列", 409)
    return keys[0], values[0]


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
    try:
        async with mysql_adapter.connection(resource, database_name) as connection:
            def query() -> tuple[dict[str, typing.Any], str]:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW COLUMNS FROM `t_global_settings`")
                    key_column, value_column = _detect_setting_columns([str(row[0]) for row in cursor.fetchall()])
                    placeholders = ",".join(["%s"] * len(keys))
                    sql = f"SELECT `{key_column}`, `{value_column}` FROM `t_global_settings` WHERE `{key_column}` IN ({placeholders})"
                    cursor.execute(sql, keys)
                    return {str(row[0]): row[1] for row in cursor.fetchall()}, f"{database_name}.t_global_settings.{key_column}/{value_column}"
            values, source = await to_thread(query)
        failed = False
        for key in keys:
            if key in values:
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, value_text=str(values[key]), source_reference=source,
                    raw_output=str(values[key]), exit_code=0, status="succeeded",
                ))
            else:
                failed = True
                snapshot.items.append(ConfigurationCaptureItem(
                    item_key=key, item_label=key, source_reference=source, raw_output="",
                    status="failed", error_message="配置项不存在",
                ))
        snapshot.status = "failed" if failed else "succeeded"
    except Exception as exc:
        snapshot.status = "failed"
        snapshot.error_message = str(exc)
    snapshot.finished_at = datetime.now(timezone.utc)
    db.flush()
    return [snapshot]


async def preview_node(db: Session, scenario: TestScenario, version: ScenarioWorkflowVersion, node: ScenarioWorkflowNode, actor_id: int) -> list[ConfigurationCaptureSnapshot]:
    if node.node_type == "server_config":
        return await capture_server(db, scenario, version, node, scope="preview", actor_id=actor_id)
    if node.node_type == "database_config":
        return await capture_database(db, scenario, version, node, scope="preview", actor_id=actor_id)
    raise WorkflowError("NODE_PREVIEW_NOT_SUPPORTED", "该节点不支持预采集", 409)
