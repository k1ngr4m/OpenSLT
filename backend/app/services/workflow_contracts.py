from __future__ import annotations

import csv
import hashlib
import io
import os
import posixpath
import re
import shlex
import tempfile
import typing
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

import asyncssh
from pymysql.cursors import SSCursor
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.database import mysql_adapter, validate_database
from app.core.compat import to_thread
from app.core.config import settings
from app.models import (
    Artifact,
    ContractDataFile,
    Resource,
    RunStep,
    ScenarioWorkflowNode,
    ScenarioWorkflowVersion,
    TestRun,
    TestScenario,
)
from app.services.order_configs import OrderConfigError, order_config_service
from app.services.resource_relations import node_config_with_relations, node_contract_file_ids
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import CONTRACT_TABLES, INTERFACE_PATTERN, WorkflowError, resource_map
from app.workflow_node_configs import OrderPreparationConfig, parse_node_config


MAX_CONTRACT_CSV_BYTES = 50 * 1024 * 1024


def _archive_order_config(
    db: Session,
    run: TestRun,
    step: RunStep,
    filename: str,
    content: str,
    checksum: str,
) -> Artifact:
    data = content.encode("utf-8")
    actual_checksum = hashlib.sha256(data).hexdigest()
    if actual_checksum != checksum:
        raise WorkflowError("ORDER_CONFIG_CHECKSUM_INVALID", "发单 XML 内容校验失败", 409)
    existing = db.scalar(
        select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.artifact_type == "order_config_xml",
        )
    )
    if existing is not None:
        existing_path = Path(existing.path)
        if (
            existing.checksum != checksum
            or not existing_path.is_file()
            or hashlib.sha256(existing_path.read_bytes()).hexdigest() != checksum
        ):
            raise WorkflowError("ORDER_CONFIG_ARCHIVE_CHANGED", "已归档的发单 XML 已丢失或发生变化", 409)
        return existing

    directory = (
        settings.artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
        / "order"
        / str(step.id)
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "order-config.xml"
    temporary = directory / f".{target.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    artifact = Artifact(
        run_id=run.id,
        step_id=step.id,
        artifact_type="order_config_xml",
        name=filename,
        path=str(target),
        content_type="application/xml; charset=utf-8",
        size=len(data),
        checksum=checksum,
        is_immutable=True,
    )
    db.add(artifact)
    db.flush()
    return artifact

def parse_read_symbol_csv(document: dict) -> int:
    matches: list[str] = []
    def visit(node: dict) -> None:
        if str(node.get("name") or "").casefold() == "read_symbol_csv":
            attrs = {item.get("name"): item.get("value") for item in node.get("attributes") or []}
            value = attrs.get("value")
            if value is None:
                value = "".join(str(child.get("text") or "") for child in node.get("children") or [] if child.get("type") in {"text", "cdata"}).strip()
            matches.append(str(value))
        for child in node.get("children") or []:
            if child.get("type") == "element":
                visit(child)
    visit(document)
    if not matches:
        return 0
    if len(matches) != 1 or matches[0] not in {"0", "1"}:
        raise WorkflowError("READ_SYMBOL_CSV_INVALID", "XML 中 read_symbol_csv 必须唯一且值为 0 或 1", 409)
    return int(matches[0])

@asynccontextmanager
async def _sftp(resource: Resource):
    connection = await asyncssh.connect(**_ssh_options(resource))
    try:
        client = await connection.start_sftp_client()
        try:
            yield client
        finally:
            client.exit()
    finally:
        connection.close()
        with suppress(Exception):
            await connection.wait_closed()


async def _write_remote_contract(resource: Resource, filename: str, source: Path) -> str:
    remote_path = posixpath.join(resource.remote_path.rstrip("/"), filename)
    temporary = posixpath.join(resource.remote_path.rstrip("/"), f".openslt-{uuid4().hex}.tmp")
    async with _sftp(resource) as client:
        try:
            await client.put(str(source), temporary)
            await client.posix_rename(temporary, remote_path)
        finally:
            with suppress(asyncssh.SFTPError):
                await client.remove(temporary)
    return remote_path


def _contract_type_from_filename(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem.casefold()
    if stem.startswith("t_close_report_opt") or re.search(r"(?:^|[_-])opt(?:[_-]|$)", stem):
        return "options", "t_close_report_opt"
    if stem.startswith("t_close_report") or re.search(r"(?:^|[_-])fut(?:[_-]|$)", stem):
        return "futures", "t_close_report"
    return "unknown", Path(filename).stem


def _inspect_contract_csv(path: Path, filename: str) -> tuple[str | None, int, list[dict[str, typing.Any]]]:
    data = path.read_bytes()
    content: str | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            content = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise WorkflowError("CONTRACT_CSV_ENCODING_INVALID", f"合约文件 {filename} 不是 UTF-8 或 GB18030 编码", 409)
    try:
        reader = csv.DictReader(io.StringIO(content, newline=""))
        row_count = 0
        quote_date: str | None = None
        preview_rows: list[dict[str, typing.Any]] = []
        for raw_row in reader:
            row = {str(key or ""): value for key, value in raw_row.items()}
            if quote_date is None:
                quote_date = next(
                    (str(value) for key, value in row.items() if key.casefold() == "quote_date" and value),
                    None,
                )
            if len(preview_rows) < 5:
                preview_rows.append(row)
            row_count += 1
    except csv.Error as exc:
        raise WorkflowError("CONTRACT_CSV_INVALID", f"合约文件 {filename} 格式错误：{exc}", 409) from exc
    return quote_date, row_count, preview_rows


async def scan_remote_contract_files(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    actor_id: int,
) -> list[ContractDataFile]:
    if node.node_type != "order_preparation":
        raise WorkflowError("ORDER_NODE_REQUIRED", "只有发单节点可以扫描合约数据", 400)
    order_resource = resource_map(db, version).get("order")
    if not order_resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "场景资源池缺少发单工具", 409)

    archive_dir = settings.artifact_root / "workflows" / str(scenario.id) / str(version.id) / node.node_key / "contracts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    discovered: list[ContractDataFile] = []
    async with _sftp(order_resource) as client:
        async for entry in client.scandir(order_resource.remote_path.rstrip("/")):
            filename = str(entry.filename or "")
            if not filename.casefold().endswith(".csv"):
                continue
            if entry.attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                continue
            size = int(entry.attrs.size or 0)
            if size > MAX_CONTRACT_CSV_BYTES:
                raise WorkflowError("CONTRACT_CSV_TOO_LARGE", f"合约文件 {filename} 超过 50 MiB", 409)
            remote_path = posixpath.join(order_resource.remote_path.rstrip("/"), filename)
            handle, temporary_name = tempfile.mkstemp(
                prefix=".openslt-contract-scan-", suffix=".csv", dir=str(archive_dir)
            )
            os.close(handle)
            temporary = Path(temporary_name)
            try:
                await client.get(remote_path, str(temporary))
                data = temporary.read_bytes()
                checksum = hashlib.sha256(data).hexdigest()
                existing = db.scalar(
                    select(ContractDataFile).where(
                        ContractDataFile.order_resource_id == order_resource.id,
                        ContractDataFile.filename == filename,
                        ContractDataFile.checksum == checksum,
                    ).order_by(ContractDataFile.id.desc())
                )
                if existing and Path(existing.archive_path).is_file():
                    discovered.append(existing)
                    continue
                quote_date, row_count, preview_rows = _inspect_contract_csv(temporary, filename)
                archive_path = archive_dir / filename
                if archive_path.exists() and hashlib.sha256(archive_path.read_bytes()).hexdigest() != checksum:
                    archive_path = archive_dir / f"{Path(filename).stem}_{checksum[:8]}.csv"
                temporary.replace(archive_path)
                contract_type, source_table = _contract_type_from_filename(filename)
                item = ContractDataFile(
                    scenario_id=scenario.id,
                    workflow_node_id=node.id,
                    order_resource_id=order_resource.id,
                    database_resource_id=None,
                    database_name=None,
                    contract_type=contract_type,
                    source_table=source_table,
                    filename=filename,
                    remote_path=remote_path,
                    archive_path=str(archive_path),
                    quote_date=quote_date,
                    row_count=row_count,
                    size=len(data),
                    checksum=checksum,
                    preview_rows=preview_rows,
                    created_by=actor_id,
                )
                db.add(item)
                db.flush()
                discovered.append(item)
            finally:
                temporary.unlink(missing_ok=True)
    return discovered


async def _export_contract_csv(
    database_resource: Resource,
    database_name: str,
    table: str,
    target: Path,
) -> tuple[str, int, list[dict[str, typing.Any]]]:
    try:
        async with mysql_adapter.connection(database_resource, database_name) as connection:
            def export() -> tuple[str, int, list[dict[str, typing.Any]]]:
                sql = f"SELECT * FROM `{table}` WHERE `quote_date` = (SELECT MAX(`quote_date`) FROM `{table}`)"
                with connection.cursor(SSCursor) as cursor, target.open(
                    "w", encoding="utf-8-sig", newline=""
                ) as output:
                    cursor.execute(sql)
                    columns = [item[0] for item in cursor.description or []]
                    if "quote_date" not in columns:
                        raise WorkflowError("CONTRACT_DATE_COLUMN_MISSING", f"{table} 缺少 quote_date 字段", 409)
                    writer = csv.writer(output)
                    writer.writerow(columns)
                    row_count = 0
                    quote_date = ""
                    preview_rows: list[dict[str, typing.Any]] = []
                    while True:
                        batch = cursor.fetchmany(1000)
                        if not batch:
                            break
                        writer.writerows(batch)
                        for values in batch:
                            row = dict(zip(columns, values))
                            if not quote_date:
                                quote_date = str(row.get("quote_date") or "")
                            if len(preview_rows) < 5:
                                preview_rows.append(row)
                        row_count += len(batch)
                    return quote_date, row_count, preview_rows

            quote_date, row_count, preview_rows = await to_thread(export)
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("CONTRACT_EXPORT_FAILED", f"{table} 导出失败: {exc}", 409) from exc
    if not row_count:
        raise WorkflowError("CONTRACT_DATA_EMPTY", f"{table} 没有可导出的数据", 409)
    return quote_date, row_count, preview_rows


async def fetch_contract_files(
    db: Session,
    scenario: TestScenario,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    database_resource: Resource,
    database_name: str,
    contract_types: list[str],
    actor_id: int,
) -> list[ContractDataFile]:
    if node.node_type != "order_preparation":
        raise WorkflowError("ORDER_NODE_REQUIRED", "只有发单节点可以获取合约数据", 400)
    resources = resource_map(db, version)
    order_resource = resources.get("order")
    if not order_resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "场景资源池缺少发单工具", 409)
    database_name = validate_database(database_resource, database_name)
    archive_dir = settings.artifact_root / "workflows" / str(scenario.id) / str(version.id) / node.node_key / "contracts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    created: list[ContractDataFile] = []
    for contract_type in list(dict.fromkeys(contract_types)):
        table = CONTRACT_TABLES.get(contract_type)
        if not table:
            raise WorkflowError("CONTRACT_TYPE_INVALID", "合约类型不受支持")
        handle, temporary_name = tempfile.mkstemp(
            prefix=".openslt-contract-", suffix=".csv", dir=str(archive_dir)
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            quote_date, row_count, preview_rows = await _export_contract_csv(
                database_resource, database_name, table, temporary
            )
            safe_date = re.sub(r"[^0-9]", "", quote_date) or "unknown"
            filename = f"{table}_{safe_date}.csv"
            archive_path = archive_dir / filename
            data = temporary.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            if archive_path.exists() and hashlib.sha256(archive_path.read_bytes()).hexdigest() != checksum:
                archive_path = archive_dir / f"{table}_{safe_date}_{checksum[:8]}.csv"
            temporary.replace(archive_path)
            remote_path = await _write_remote_contract(order_resource, archive_path.name, archive_path)
        finally:
            temporary.unlink(missing_ok=True)
        existing = db.scalar(select(ContractDataFile).where(
            ContractDataFile.workflow_node_id == node.id,
            ContractDataFile.filename == archive_path.name,
            ContractDataFile.checksum == checksum,
        ))
        if existing:
            created.append(existing)
            continue
        item = ContractDataFile(
            scenario_id=scenario.id, workflow_node_id=node.id, order_resource_id=order_resource.id,
            database_resource_id=database_resource.id, database_name=database_name,
            contract_type=contract_type, source_table=table, filename=archive_path.name,
            remote_path=remote_path, archive_path=str(archive_path), quote_date=quote_date,
            row_count=row_count, size=archive_path.stat().st_size, checksum=checksum,
            preview_rows=[{key: str(value) if value is not None else None for key, value in row.items()} for row in preview_rows],
            created_by=actor_id,
        )
        db.add(item)
        created.append(item)
    db.flush()
    return created


async def prepare_order_node(
    db: Session,
    version: ScenarioWorkflowVersion,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
    *,
    run: typing.Optional[TestRun] = None,
    step: typing.Optional[RunStep] = None,
) -> dict:
    resource = run_resources.get("order")
    if not resource:
        raise WorkflowError("ORDER_RESOURCE_REQUIRED", "运行资源缺少发单工具", 409)
    config = typing.cast(
        OrderPreparationConfig,
        parse_node_config(node.node_type, node_config_with_relations(node)),
    )
    try:
        detail = await order_config_service.read(resource, config.xml_filename)
    except OrderConfigError as exc:
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
    expected = config.xml_checksum
    if expected and detail["checksum"] != expected:
        raise WorkflowError("ORDER_CONFIG_CHANGED", "XML 配置校验值与发布版本不一致", 409)
    xml_artifact = None
    if run is not None and step is not None:
        xml_artifact = _archive_order_config(
            db,
            run,
            step,
            str(detail["name"]),
            str(detail["content"]),
            str(detail["checksum"]),
        )
    read_csv = parse_read_symbol_csv(detail["document"])
    file_summaries = []
    if read_csv:
        file_ids = config.contract_file_ids
        files = list(db.scalars(select(ContractDataFile).where(ContractDataFile.id.in_(file_ids))).all())
        if len(files) != len(set(file_ids)):
            raise WorkflowError("CONTRACT_FILES_INVALID", "合约 CSV 不存在", 409)
        for item in files:
            archive = Path(item.archive_path)
            if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != item.checksum:
                raise WorkflowError("CONTRACT_FILE_CHANGED", f"合约文件 {item.filename} 已丢失或校验失败", 409)
            remote_path = await _write_remote_contract(resource, item.filename, archive)
            file_summaries.append({
                "id": item.id, "filename": item.filename, "remote_path": remote_path,
                "quote_date": item.quote_date, "row_count": item.row_count, "checksum": item.checksum,
            })
    interface = config.network_interface
    if interface and not INTERFACE_PATTERN.fullmatch(interface):
        raise WorkflowError("NETWORK_INTERFACE_INVALID", "网卡接口名称不合法", 409)
    binary = (resource.capabilities or {}).get("order_tool") or posixpath.basename(resource.remote_path.rstrip("/"))
    command_parts = [f"cd {shlex.quote(resource.remote_path)}"]
    if interface:
        command_parts.append(f"export ZF_ATTR=interface={interface}")
    command_parts.append(f"{shlex.quote('./' + binary)} {shlex.quote(detail['name'])}")
    return {
        "prepared": True,
        "xml_filename": detail["name"],
        "xml_checksum": detail["checksum"],
        "xml_artifact_id": xml_artifact.id if xml_artifact else None,
        "read_symbol_csv": read_csv,
        "network_interface": interface or None,
        "order_action": config.order_action,
        "contract_files": file_summaries,
        "generated_command": " && ".join(command_parts),
        "process_started": False,
    }
