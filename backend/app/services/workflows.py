from __future__ import annotations

import csv
import hashlib
import posixpath
import shlex
import tempfile
import typing
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import asyncssh
from pymysql.cursors import SSCursor
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.database import DatabaseOperationError, mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.config import settings
from app.core.time import beijing_now
from app.models import Artifact, Resource, RunStep, ScenarioWorkflowNode, TestRun
from app.services.order_configs import (
    OrderConfigError,
    order_config_service,
    parser_config_role,
    parser_main_config_filename,
)
from app.services.parser_inputs import PARSER_TABLES, parser_config_database_name, parser_table_database_name
from app.services.workflow_capture import _ssh_options, capture_database, capture_server, preview_node
from app.services.workflow_contracts import _sftp, fetch_contract_files, parse_read_symbol_csv, prepare_order_node
from app.services.workflow_core import (
    NODE_TYPES,
    SLNIC_NODE_TYPES,
    WorkflowError,
    clone_published_to_draft,
    copy_version_contents,
    create_draft,
    create_next_version,
    is_version_head,
    load_version,
    replace_draft,
    resource_map,
    validate_structure,
    version_heads_query,
    workflow_payload,
)
from app.services.workflow_publishing import publish, validate_publish
from app.workflow_node_configs import ParserConfig, ShellCommandsConfig, parse_node_config

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


def _failure_stopping_shell(commands: typing.Sequence[str]) -> str:
    lines: typing.List[str] = []
    for command in commands:
        lines.extend([
            command,
            "openslt_slnic_status=$?",
            '[ "$openslt_slnic_status" -eq 0 ] || exit "$openslt_slnic_status"',
        ])
    return "\n".join(lines) + "\n"


async def _run_slnic_commands(
    connection: typing.Any,
    workdir: str,
    commands: typing.Sequence[str],
) -> None:
    script = _failure_stopping_shell(commands)
    shell_command = f"cd {shlex.quote(workdir)} && /bin/sh -c {shlex.quote(script)}"
    result = await connection.run(shell_command, check=False)
    if result.exit_status == 0:
        return
    detail = str(result.stderr or result.stdout or "远端命令没有返回错误信息").strip()[:1000]
    raise WorkflowError(
        "SLNIC_COMMAND_FAILED",
        f"SLNIC 命令执行失败（退出码 {result.exit_status}）：{detail}",
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
    config = typing.cast(
        ShellCommandsConfig,
        parse_node_config(node.node_type, step.config_snapshot or {}),
    )
    if not config.commands:
        raise WorkflowError("SLNIC_COMMANDS_REQUIRED", "SLNIC 节点至少需要一条命令", 409)

    root = resource.remote_path.strip().rstrip("/") or "/"
    workdir = posixpath.join(root, "tcpdump")
    commands = list(config.commands)
    summary = {
        "resource_id": resource.id,
        "resource_name": resource.name,
        "remote_workdir": workdir,
        "commands": commands,
        "exit_code": 0,
    }
    connection = None
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        await _run_slnic_commands(connection, workdir, commands)
        if node.node_type == "slnic_merge_capture":
            summary.update(
                await collect_slnic_merge_artifact(
                    db,
                    run,
                    step,
                    resource,
                    connection=connection,
                )
            )
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


def _parser_input_directory(run: TestRun, step: RunStep) -> Path:
    return _parser_artifact_directory(run, step) / "inputs"


def _parser_input_exports(step: RunStep) -> dict[str, dict[str, typing.Any]]:
    raw = (step.result_summary or {}).get("parser_input_exports")
    if not isinstance(raw, dict):
        return {}
    return {
        table: dict(value)
        for table, value in raw.items()
        if table in PARSER_TABLES and isinstance(value, dict)
    }


def resolve_parser_table_database(
    database_resource: Resource,
    trading_database_name: str,
    table: str,
) -> str:
    selected = parser_table_database_name(table, trading_database_name)
    if table not in PARSER_TABLES:
        raise WorkflowError("PARSER_TABLE_INVALID", f"不支持导出数据表 {table}", 400)
    if not selected:
        raise WorkflowError(
            "PARSER_CONFIG_DATABASE_INVALID",
            "运行数据库名称必须以 _trading_data 结尾，才能匹配配置数据库",
            409,
        )
    try:
        return validate_database(database_resource, selected)
    except DatabaseOperationError as exc:
        if table == "t_account_exchange_code":
            expected = parser_config_database_name(trading_database_name)
            raise WorkflowError(
                "PARSER_CONFIG_DATABASE_REQUIRED",
                f"数据库资源缺少配套配置库 {expected}",
                409,
            ) from exc
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc


def _parser_input_artifact(
    db: Session,
    run: TestRun,
    step: RunStep,
    table: str,
) -> typing.Optional[Artifact]:
    return db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.artifact_type == "parser_input_csv",
            Artifact.name == f"{table}.csv",
        )
    )


def _register_parser_input_artifact(
    db: Session,
    run: TestRun,
    step: RunStep,
    table: str,
    target: Path,
) -> Artifact:
    data = target.read_bytes()
    artifact = _parser_input_artifact(db, run, step, table)
    if artifact is None:
        artifact = Artifact(
            run_id=run.id,
            step_id=step.id,
            artifact_type="parser_input_csv",
            name=target.name,
            path=str(target),
        )
        db.add(artifact)
    artifact.path = str(target)
    artifact.content_type = "text/csv"
    artifact.size = len(data)
    artifact.checksum = hashlib.sha256(data).hexdigest()
    artifact.is_immutable = False
    db.flush()
    return artifact


async def export_parser_table_snapshot(
    db: Session,
    run: TestRun,
    step: RunStep,
    database_resource: Resource,
    database_name: str,
    table: str,
    *,
    source: str,
    actor_id: typing.Optional[int] = None,
) -> dict[str, typing.Any]:
    if table not in PARSER_TABLES:
        raise WorkflowError("PARSER_TABLE_INVALID", f"不支持导出数据表 {table}", 400)
    directory = _parser_input_directory(run, step)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{table}.csv"
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
    try:
        row_count = await _export_parser_table(
            database_resource,
            database_name,
            table,
            temporary,
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    artifact = _register_parser_input_artifact(db, run, step, table, target)
    exported_at = beijing_now()
    detail = {
        "artifact_id": artifact.id,
        "filename": artifact.name,
        "database_name": database_name,
        "row_count": row_count,
        "size": artifact.size,
        "checksum": artifact.checksum,
        "source": source,
        "exported_by": actor_id,
        "exported_at": exported_at.isoformat(),
    }
    exports = _parser_input_exports(step)
    exports[table] = detail
    step.result_summary = {
        **(step.result_summary or {}),
        "parser_input_exports": exports,
    }
    db.flush()
    return {"table": table, **detail, "artifact": artifact}


def _parser_pcap_artifact(db: Session, run: TestRun, step: RunStep) -> Artifact:
    prior_steps = sorted(
        (
            item for item in run.steps
            if item.node_type == "slnic_merge_capture"
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
    raise WorkflowError("PARSER_PCAP_REQUIRED", "本次运行尚无 SLNIC 节点生成的 merge_pcap.pcapng", 409)


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


async def _load_parser_xml_files(
    resource: Resource,
    raw_config: typing.Mapping[str, typing.Any],
) -> dict[str, dict[str, typing.Any]]:
    selections = {
        "config": (
            str(raw_config.get("config_xml_filename") or "config.xml").strip(),
            str(raw_config.get("config_xml_checksum") or "").strip(),
        ),
        "instance": (
            str(raw_config.get("instance_xml_filename") or "instance.xml").strip(),
            str(raw_config.get("instance_xml_checksum") or "").strip(),
        ),
        "analysis": (
            str(raw_config.get("analysis_xml_filename") or parser_main_config_filename(resource)).strip(),
            str(raw_config.get("analysis_xml_checksum") or "").strip(),
        ),
    }
    loaded: dict[str, dict[str, typing.Any]] = {}
    for role, (filename, expected_checksum) in selections.items():
        if parser_config_role(filename) != role:
            raise WorkflowError("PARSER_CONFIG_INVALID", f"解析 {role} XML 文件类型不正确", 409)
        try:
            detail = await order_config_service.read(resource, filename)
        except OrderConfigError as exc:
            raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
        if expected_checksum and detail["checksum"] != expected_checksum:
            raise WorkflowError("PARSER_CONFIG_CHANGED", f"解析配置 {filename} 已发生变化", 409)
        loaded[role] = detail
    return loaded


def _valid_parser_input_snapshot(
    db: Session,
    run: TestRun,
    step: RunStep,
    table: str,
    database_name: str,
) -> typing.Optional[tuple[Artifact, dict[str, typing.Any]]]:
    detail = _parser_input_exports(step).get(table)
    if not detail or detail.get("database_name") != database_name:
        return None
    artifact = _parser_input_artifact(db, run, step, table)
    if not artifact or artifact.id != detail.get("artifact_id"):
        return None
    path = Path(artifact.path)
    if not path.is_file():
        return None
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != artifact.checksum or checksum != detail.get("checksum"):
        return None
    return artifact, detail


async def execute_parser_node(
    db: Session,
    run: TestRun,
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
    append_log_callback: typing.Optional[typing.Callable[..., typing.Any]] = None,
) -> dict:
    parser_resource = run_resources.get("parser")
    database_resource = run_resources.get("database")
    if not parser_resource or parser_resource.is_deleted or not parser_resource.is_enabled:
        raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少已启用的解析工具", 409)
    if not database_resource:
        raise WorkflowError("PARSER_DATABASE_REQUIRED", "运行资源缺少数据库", 409)
    raw_config = step.config_snapshot or node.config or {}
    config = typing.cast(ParserConfig, parse_node_config(node.node_type, raw_config))
    database_name = config.database_name.strip()
    try:
        database_name = validate_database(database_resource, database_name)
    except DatabaseOperationError as exc:
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
    table_databases = {
        table: resolve_parser_table_database(database_resource, database_name, table)
        for table in PARSER_TABLES
    }
    capabilities = parser_resource.capabilities or {}
    binary = str(capabilities.get("parser_binary") or capabilities.get("parser_tool") or "").strip()
    directory = parser_resource.remote_path.strip().rstrip("/")
    if not binary or not directory:
        raise WorkflowError("PARSER_RESOURCE_INVALID", "解析工具资源配置不完整", 409)
    xml_files = await _load_parser_xml_files(parser_resource, raw_config)
    analysis_filename = str(xml_files["analysis"]["name"])
    pcap_artifact = _parser_pcap_artifact(db, run, step)
    artifact_directory = _parser_artifact_directory(run, step)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    remote_workdir = posixpath.join(
        directory,
        ".openslt-runs",
        f"r{run.id}-s{step.id}-a{step.retry_count}-{uuid4().hex[:8]}",
    )
    binary_path = posixpath.join(directory, binary)
    command = (
        f"cd {shlex.quote(remote_workdir)} && "
        f"{shlex.quote(binary_path)} {shlex.quote(analysis_filename)}"
    )
    table_rows: dict[str, int] = {}
    input_checksums: dict[str, str] = {}
    output_artifacts: list[Artifact] = []
    stdout = ""
    stderr = ""
    started_at = beijing_now()

    with tempfile.TemporaryDirectory(prefix="openslt-parser-") as temporary_name:
        staging = Path(temporary_name)
        input_files: dict[str, Path] = {}
        for table in PARSER_TABLES:
            table_database_name = table_databases[table]
            snapshot = _valid_parser_input_snapshot(db, run, step, table, table_database_name)
            if snapshot is None:
                exported = await export_parser_table_snapshot(
                    db,
                    run,
                    step,
                    database_resource,
                    table_database_name,
                    table,
                    source="auto",
                )
                if append_log_callback:
                    row_count = int(exported.get("row_count") or 0)
                    append_log_callback(
                        db,
                        run,
                        "parser.table_skipped" if row_count == 0 else "parser.table_exported",
                        f"{table} 没有记录，已跳过" if row_count == 0 else f"已自动导出 {table}",
                        step=step,
                        source="parser",
                        detail={key: exported[key] for key in ("table", "artifact_id", "row_count", "checksum", "source")},
                    )
                snapshot = _valid_parser_input_snapshot(db, run, step, table, table_database_name)
            if snapshot is None:
                raise WorkflowError("PARSER_INPUT_MISSING", f"未能准备解析输入 {table}.csv", 409)
            artifact, detail = snapshot
            artifact.is_immutable = True
            table_rows[table] = int(detail.get("row_count") or 0)
            input_files[artifact.name] = Path(artifact.path)
        input_files["merge_pcap.pcapng"] = Path(pcap_artifact.path)
        xml_staging = {
            "config.xml": xml_files["config"],
            "instance.xml": xml_files["instance"],
            analysis_filename: xml_files["analysis"],
        }
        for filename, detail in xml_staging.items():
            target = staging / filename
            target.write_text(str(detail["content"]), encoding="utf-8")
            input_files[filename] = target
        for filename, source in input_files.items():
            input_checksums[filename] = hashlib.sha256(source.read_bytes()).hexdigest()

        connection = None
        sftp = None
        try:
            connection = await asyncssh.connect(**_ssh_options(parser_resource))
            sftp = await connection.start_sftp_client()
            await sftp.makedirs(remote_workdir, exist_ok=True)
            for filename, source in input_files.items():
                await _upload_parser_input(sftp, remote_workdir, filename, source)
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
            after = await _parser_csv_snapshot(sftp, remote_workdir)
            changed = sorted(
                name for name in after
                if name not in input_files
            )
            if not changed:
                raise WorkflowError("PARSER_OUTPUT_MISSING", "解析成功但没有生成或更新 CSV 文件", 409)
            for filename in changed:
                target = artifact_directory / filename
                partial = target.with_name(f".{target.name}.{uuid4().hex}.part")
                try:
                    await sftp.get(posixpath.join(remote_workdir, filename), str(partial))
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
    duration_ms = int((beijing_now() - started_at).total_seconds() * 1000)
    return {
        **(step.result_summary or {}),
        "resource_id": parser_resource.id,
        "database_name": database_name,
        "config_database_name": table_databases["t_account_exchange_code"],
        "parser_xml_files": {
            role: {"filename": detail["name"], "checksum": detail["checksum"]}
            for role, detail in xml_files.items()
        },
        "remote_workdir": remote_workdir,
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
