from __future__ import annotations

import csv
import hashlib
import posixpath
import shlex
import tempfile
import typing
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncssh
from pymysql.cursors import SSCursor
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.database import DatabaseOperationError, mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.config import settings
from app.models import Artifact, Resource, RunStep, ScenarioWorkflowNode, TestRun
from app.services.order_configs import parser_main_config_filename
from app.services.workflow_capture import _ssh_options, capture_database, capture_server, preview_node
from app.services.workflow_contracts import _sftp, fetch_contract_files, parse_read_symbol_csv, prepare_order_node
from app.services.workflow_core import (
    NODE_TYPES,
    SLNIC_NODE_TYPES,
    WorkflowError,
    clone_published_to_draft,
    copy_version_contents,
    create_draft,
    load_version,
    replace_draft,
    resource_map,
    validate_structure,
    workflow_payload,
)
from app.services.workflow_publishing import publish, validate_publish
from app.workflow_node_configs import ParserConfig, parse_node_config

PARSER_TABLES = ("t_fut_orders", "t_fut_quotes", "t_fut_arbi_orders")

def _slnic_artifact_path(run: TestRun, step: RunStep) -> Path:
    return (
        settings.artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
        / "slnic"
        / str(step.id)
        / "merge_pcap.pcapng"
    )


def _register_slnic_merge_artifact(db: Session, run: TestRun, step: RunStep, target: Path) -> dict:
    data = target.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.name == target.name,
        )
    )
    if artifact is None:
        artifact = Artifact(
            run_id=run.id,
            step_id=step.id,
            artifact_type="packet_capture",
            name=target.name,
            path=str(target),
        )
        db.add(artifact)
    artifact.artifact_type = "packet_capture"
    artifact.path = str(target)
    artifact.content_type = "application/vnd.tcpdump.pcap"
    artifact.size = len(data)
    artifact.checksum = checksum
    artifact.is_immutable = True
    db.flush()
    return {
        "artifact_id": artifact.id,
        "filename": artifact.name,
        "checksum": artifact.checksum,
        "size": artifact.size,
    }


async def collect_slnic_merge_artifact(
    db: Session,
    run: TestRun,
    step: RunStep,
    resource: Resource,
    connection: typing.Any = None,
) -> dict:
    if not resource or resource.is_deleted or not resource.is_enabled:
        raise WorkflowError("SLNIC_RESOURCE_REQUIRED", "运行资源缺少已启用的 SLNIC 节点", 409)
    if not resource.remote_path.strip():
        raise WorkflowError("SLNIC_REMOTE_PATH_REQUIRED", "SLNIC 资源未配置远端路径", 409)

    target = _slnic_artifact_path(run, step)
    workdir = posixpath.join(resource.remote_path.rstrip("/"), "tcpdump")
    remote_file = posixpath.join(workdir, "merge_pcap.pcapng")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
    owns_connection = connection is None
    sftp = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if connection is None:
            connection = await asyncssh.connect(**_ssh_options(resource))
        sftp = await connection.start_sftp_client()
        await sftp.get(remote_file, str(temporary))
        temporary.replace(target)
    except Exception as exc:
        raise WorkflowError("SLNIC_ARTIFACT_COLLECT_FAILED", f"拉取合并后的 pcapng 失败：{exc}", 409) from exc
    finally:
        temporary.unlink(missing_ok=True)
        if sftp:
            with suppress(Exception):
                sftp.exit()
        if owns_connection and connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()

    return _register_slnic_merge_artifact(db, run, step, target)


async def _run_slnic_command(connection: typing.Any, command: str, label: str) -> None:
    result = await connection.run(command, check=False)
    if result.exit_status == 0:
        return
    detail = str(result.stderr or result.stdout or "远端命令没有返回错误信息").strip()[:1000]
    raise WorkflowError(
        "SLNIC_COMMAND_FAILED",
        f"{label}失败（退出码 {result.exit_status}）：{detail}",
        409,
    )


async def execute_slnic_node(
    db: Session,
    run: TestRun,
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
) -> dict:
    resource = run_resources.get("slnic")
    if not resource or resource.is_deleted or not resource.is_enabled:
        raise WorkflowError("SLNIC_RESOURCE_REQUIRED", "运行资源缺少已启用的 SLNIC 节点", 409)
    if node.node_type not in SLNIC_NODE_TYPES:
        raise WorkflowError("SLNIC_NODE_REQUIRED", "当前节点不是 SLNIC 节点", 400)
    if not resource.remote_path.strip():
        raise WorkflowError("SLNIC_REMOTE_PATH_REQUIRED", "SLNIC 资源未配置远端路径", 409)

    summary = {"resource_id": resource.id, "exit_code": 0}
    workdir = posixpath.join(resource.remote_path.rstrip("/"), "tcpdump")
    prefix = f"cd {shlex.quote(workdir)} && "
    connection = None
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        if node.node_type == "slnic_start_capture":
            await _run_slnic_command(
                connection, prefix + "./start_slnic_dump.sh", "启动 SLNIC 抓包"
            )
            return summary
        if node.node_type == "slnic_stop_capture":
            await _run_slnic_command(
                connection, prefix + "./stop_slnic_dump.sh", "关闭 SLNIC 抓包"
            )
            return summary

        await _run_slnic_command(
            connection, prefix + "./pcap_mergetoo slnic*", "合并 SLNIC 抓包"
        )
        await _run_slnic_command(
            connection,
            prefix
            + "if [ ! -f merge_pcap.pcap ] && [ -f merge_pacp.pcap ]; "
            + "then mv -- merge_pacp.pcap merge_pcap.pcap; fi; "
            + "test -f merge_pcap.pcap",
            "检查合并后的 pcap 文件",
        )
        await _run_slnic_command(
            connection,
            prefix + "./editcap merge_pcap.pcap merge_pcap.pcapng && test -f merge_pcap.pcapng",
            "转换 pcapng 文件",
        )
        summary.update(await collect_slnic_merge_artifact(db, run, step, resource, connection=connection))
        return summary
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("SLNIC_EXECUTION_FAILED", f"SLNIC 节点执行失败：{exc}", 409) from exc
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def _export_parser_table(
    database_resource: Resource,
    database_name: str,
    table: str,
    target: Path,
) -> int:
    if table not in PARSER_TABLES:
        raise WorkflowError("PARSER_TABLE_INVALID", f"不支持导出数据表 {table}", 400)
    try:
        async with mysql_adapter.connection(database_resource, database_name) as connection:
            def export() -> int:
                with connection.cursor(SSCursor) as cursor, target.open(
                    "w", encoding="utf-8", newline=""
                ) as output:
                    cursor.execute(f"SELECT * FROM `{table}`")
                    columns = [item[0] for item in cursor.description or []]
                    if not columns:
                        return 0
                    writer = csv.writer(output)
                    writer.writerow(columns)
                    row_count = 0
                    while True:
                        batch = cursor.fetchmany(1000)
                        if not batch:
                            break
                        writer.writerows(batch)
                        row_count += len(batch)
                    return row_count

            row_count = await to_thread(export)
    except Exception as exc:
        raise WorkflowError(
            "PARSER_DATABASE_EXPORT_FAILED", f"导出 {table} 失败：{exc}", 409
        ) from exc
    if not row_count:
        raise WorkflowError("PARSER_TABLE_EMPTY", f"数据表 {table} 没有可导出的记录", 409)
    return row_count


def _parser_artifact_directory(run: TestRun, step: RunStep) -> Path:
    return (
        settings.artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
        / "parser"
        / str(step.id)
    )


def _parser_pcap_artifact(db: Session, run: TestRun, step: RunStep) -> Artifact:
    prior_steps = sorted(
        (
            item for item in run.steps
            if item.position < step.position
            and item.node_type == "slnic_merge_capture"
            and item.status == "succeeded"
        ),
        key=lambda item: item.position,
        reverse=True,
    )
    for prior in prior_steps:
        artifact = db.scalar(
            select(Artifact).where(
                Artifact.run_id == run.id,
                Artifact.step_id == prior.id,
                Artifact.name == "merge_pcap.pcapng",
            )
        )
        if artifact and Path(artifact.path).is_file():
            return artifact
    raise WorkflowError("PARSER_PCAP_REQUIRED", "未找到前置 SLNIC 节点生成的 merge_pcap.pcapng", 409)


async def _parser_csv_snapshot(sftp: typing.Any, directory: str) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    async for entry in sftp.scandir(directory):
        if not entry.filename.lower().endswith(".csv"):
            continue
        if entry.attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
            continue
        snapshot[entry.filename] = (entry.attrs.size or 0, entry.attrs.mtime or 0)
    return snapshot


async def _upload_parser_input(
    sftp: typing.Any, directory: str, filename: str, source: Path
) -> str:
    target = posixpath.join(directory, filename)
    temporary = posixpath.join(directory, f".openslt-{uuid4().hex}.tmp")
    try:
        await sftp.put(str(source), temporary)
        await sftp.posix_rename(temporary, target)
    except Exception as exc:
        raise WorkflowError("PARSER_INPUT_UPLOAD_FAILED", f"上传 {filename} 失败：{exc}", 409) from exc
    finally:
        with suppress(Exception):
            await sftp.remove(temporary)
    return target


def _register_parser_artifact(
    db: Session, run: TestRun, step: RunStep, target: Path
) -> Artifact:
    data = target.read_bytes()
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.name == target.name,
        )
    )
    if artifact is None:
        artifact = Artifact(run_id=run.id, step_id=step.id, name=target.name, path=str(target))
        db.add(artifact)
    artifact.artifact_type = "parsed_csv"
    artifact.path = str(target)
    artifact.content_type = "text/csv"
    artifact.size = len(data)
    artifact.checksum = hashlib.sha256(data).hexdigest()
    artifact.is_immutable = True
    db.flush()
    return artifact


async def execute_parser_node(
    db: Session,
    run: TestRun,
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
) -> dict:
    parser_resource = run_resources.get("parser")
    database_resource = run_resources.get("database")
    if not parser_resource or parser_resource.is_deleted or not parser_resource.is_enabled:
        raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少已启用的解析工具", 409)
    if not database_resource:
        raise WorkflowError("PARSER_DATABASE_REQUIRED", "运行资源缺少数据库", 409)
    config = typing.cast(ParserConfig, parse_node_config(node.node_type, node.config or {}))
    database_name = config.database_name.strip()
    try:
        database_name = validate_database(database_resource, database_name)
    except DatabaseOperationError as exc:
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
    capabilities = parser_resource.capabilities or {}
    binary = str(capabilities.get("parser_binary") or capabilities.get("parser_tool") or "").strip()
    config_filename = parser_main_config_filename(parser_resource)
    directory = parser_resource.remote_path.strip().rstrip("/")
    if not binary or not directory or not config_filename:
        raise WorkflowError("PARSER_RESOURCE_INVALID", "解析工具资源配置不完整", 409)
    pcap_artifact = _parser_pcap_artifact(db, run, step)
    artifact_directory = _parser_artifact_directory(run, step)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    command = (
        f"cd {shlex.quote(directory)} && "
        f"{shlex.quote('./' + binary)} {shlex.quote(config_filename)}"
    )
    table_rows: dict[str, int] = {}
    input_checksums: dict[str, str] = {}
    output_artifacts: list[Artifact] = []
    stdout = ""
    stderr = ""
    started_at = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory(prefix="openslt-parser-") as temporary_name:
        staging = Path(temporary_name)
        input_files: dict[str, Path] = {}
        for table in PARSER_TABLES:
            target = staging / f"{table}.csv"
            table_rows[table] = await _export_parser_table(
                database_resource, database_name, table, target
            )
            input_files[target.name] = target
        input_files["merge_pcap.pcapng"] = Path(pcap_artifact.path)
        for filename, source in input_files.items():
            input_checksums[filename] = hashlib.sha256(source.read_bytes()).hexdigest()

        connection = None
        sftp = None
        try:
            connection = await asyncssh.connect(**_ssh_options(parser_resource))
            sftp = await connection.start_sftp_client()
            await sftp.makedirs(directory, exist_ok=True)
            before = await _parser_csv_snapshot(sftp, directory)
            for filename, source in input_files.items():
                await _upload_parser_input(sftp, directory, filename, source)
            result = await connection.run(command, check=False)
            stdout = str(result.stdout or "")
            stderr = str(result.stderr or "")
            if result.exit_status != 0:
                detail = (stderr or stdout or "远端命令没有返回错误信息").strip()[:1000]
                raise WorkflowError(
                    "PARSER_COMMAND_FAILED",
                    f"解析命令失败（退出码 {result.exit_status}）：{detail}",
                    409,
                )
            after = await _parser_csv_snapshot(sftp, directory)
            changed = sorted(
                name for name, state in after.items()
                if name not in input_files and (name not in before or before[name] != state)
            )
            if not changed:
                raise WorkflowError("PARSER_OUTPUT_MISSING", "解析成功但没有生成或更新 CSV 文件", 409)
            for filename in changed:
                target = artifact_directory / filename
                partial = target.with_name(f".{target.name}.{uuid4().hex}.part")
                try:
                    await sftp.get(posixpath.join(directory, filename), str(partial))
                    partial.replace(target)
                except Exception as exc:
                    raise WorkflowError(
                        "PARSER_OUTPUT_DOWNLOAD_FAILED", f"下载 {filename} 失败：{exc}", 409
                    ) from exc
                finally:
                    partial.unlink(missing_ok=True)
                output_artifacts.append(_register_parser_artifact(db, run, step, target))
        except WorkflowError:
            raise
        except Exception as exc:
            raise WorkflowError("PARSER_EXECUTION_FAILED", f"解析节点执行失败：{exc}", 409) from exc
        finally:
            if sftp:
                with suppress(Exception):
                    sftp.exit()
            if connection:
                connection.close()
                with suppress(Exception):
                    await connection.wait_closed()

    if not output_artifacts:
        raise WorkflowError("PARSER_OUTPUT_MISSING", "解析节点没有产生 CSV 产物", 409)
    duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    return {
        "resource_id": parser_resource.id,
        "database_name": database_name,
        "table_rows": table_rows,
        "input_checksums": input_checksums,
        "pcap_artifact_id": pcap_artifact.id,
        "command": command,
        "exit_code": 0,
        "duration_ms": duration_ms,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "artifact_ids": [item.id for item in output_artifacts],
        "output_files": [item.name for item in output_artifacts],
    }
