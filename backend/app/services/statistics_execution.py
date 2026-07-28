from __future__ import annotations

import hashlib
import json
import math
import posixpath
import shlex
import typing
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import asyncssh
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing_extensions import Literal

from app.core.config import settings
from app.core.time import beijing_now
from app.models import Artifact, Metric, Resource, RunStep, ScenarioWorkflowNode, TestRun
from app.services.statistics_scripts import StatisticsScriptError, statistics_script_service
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import WorkflowError
from app.workflow_node_configs import StatisticsConfig, parse_node_config


MAX_STATISTICS_OUTPUT_BYTES = 1024 * 1024
MAX_STATISTICS_METRICS = 100


class ExcludedCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    above_limit: int = Field(default=0, ge=0)
    negative: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)


class StatisticsMetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=64)
    value: float

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("指标值必须为有限数值")
        return value


class StatisticsScriptOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source_file: str = Field(min_length=1, max_length=255)
    unit: Literal["ns"]
    sample_count: int = Field(ge=0)
    excluded_counts: ExcludedCounts = Field(default_factory=ExcludedCounts)
    metrics: typing.List[StatisticsMetricResult] = Field(min_length=1, max_length=MAX_STATISTICS_METRICS)

    @model_validator(mode="after")
    def unique_metric_keys(self) -> "StatisticsScriptOutput":
        keys = [item.key for item in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("指标 key 不能重复")
        return self


def _source_parser_step(run: TestRun, step: RunStep, parser_node_key: str) -> RunStep:
    source = next(
        (
            item for item in run.steps
            if item.code == parser_node_key
            and item.node_type == "parser_parse"
            and item.position < step.position
        ),
        None,
    )
    if source is None:
        raise WorkflowError("STATISTICS_PARSER_STEP_REQUIRED", "未找到配置的前置数据解析节点", 409)
    return source


def _validated_input_artifacts(
    db: Session,
    run: TestRun,
    step: RunStep,
    artifact_ids: typing.Iterable[int],
) -> list[Artifact]:
    ids = list(artifact_ids)
    if not ids:
        raise WorkflowError("STATISTICS_INPUTS_REQUIRED", "请至少选择一个解析 CSV", 409)
    if len(ids) != len(set(ids)):
        raise WorkflowError("STATISTICS_INPUTS_DUPLICATE", "统计输入不能重复", 400)
    config = typing.cast(
        StatisticsConfig, parse_node_config("data_statistics", step.config_snapshot or {})
    )
    source = _source_parser_step(run, step, config.parser_node_key)
    artifacts = list(db.scalars(select(Artifact).where(Artifact.id.in_(ids))).all())
    by_id = {item.id: item for item in artifacts}
    ordered: list[Artifact] = []
    for artifact_id in ids:
        artifact = by_id.get(artifact_id)
        if (
            artifact is None
            or artifact.run_id != run.id
            or artifact.step_id != source.id
            or artifact.artifact_type != "parsed_csv"
            or not artifact.is_immutable
        ):
            raise WorkflowError(
                "STATISTICS_INPUT_INVALID", "只能选择关联数据解析节点生成的不可变 CSV", 409
            )
        path = Path(artifact.path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.checksum:
            raise WorkflowError(
                "STATISTICS_INPUT_CHANGED", f"统计输入 {artifact.name} 已丢失或校验失败", 409
            )
        ordered.append(artifact)
    return ordered


def select_statistics_inputs(
    db: Session,
    run: TestRun,
    step: RunStep,
    artifact_ids: list[int],
    actor_id: int,
) -> dict[str, typing.Any]:
    artifacts = _validated_input_artifacts(db, run, step, artifact_ids)
    selected_at = beijing_now()
    inputs = [
        {
            "artifact_id": item.id,
            "filename": item.name,
            "size": item.size,
            "checksum": item.checksum,
        }
        for item in artifacts
    ]
    selection = {
        "inputs": inputs,
        "selected_by": actor_id,
        "selected_at": selected_at.isoformat(),
    }
    step.result_summary = {
        **(step.result_summary or {}),
        "statistics_selection": selection,
    }
    db.flush()
    return selection


def require_statistics_selection(db: Session, run: TestRun, step: RunStep) -> list[Artifact]:
    selection = (step.result_summary or {}).get("statistics_selection") or {}
    artifact_ids = [item.get("artifact_id") for item in selection.get("inputs") or []]
    if any(not isinstance(item, int) for item in artifact_ids):
        raise WorkflowError("STATISTICS_INPUTS_REQUIRED", "请重新选择统计输入", 409)
    return _validated_input_artifacts(db, run, step, typing.cast(typing.List[int], artifact_ids))


def _artifact_directory(run: TestRun, step: RunStep) -> Path:
    return (
        settings.artifact_root
        / run.business_code
        / str(run.plan_id)
        / str(run.scenario_id)
        / run.run_number
        / "statistics"
        / str(step.id)
    )


def _register_result_artifact(
    db: Session, run: TestRun, step: RunStep, result: dict[str, typing.Any]
) -> Artifact:
    directory = _artifact_directory(run, step)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "statistics-result.json"
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
    data = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
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
    artifact.artifact_type = "statistics_result_json"
    artifact.path = str(target)
    artifact.content_type = "application/json"
    artifact.size = len(data)
    artifact.checksum = hashlib.sha256(data).hexdigest()
    artifact.is_immutable = True
    db.flush()
    return artifact


def _metric_name(step: RunStep, filename: str, metric: StatisticsMetricResult) -> str:
    value = f"{step.name}/{filename}/{metric.label}"
    if len(value) <= 128:
        return value
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{value[:117]}-{suffix}"


def _replace_metrics(
    db: Session,
    run: TestRun,
    step: RunStep,
    artifacts: dict[str, Artifact],
    results: list[StatisticsScriptOutput],
    script_detail: dict[str, typing.Any],
    max_latency_ns: int,
) -> None:
    existing = list(db.scalars(select(Metric).where(Metric.run_id == run.id)).all())
    for metric in existing:
        if (metric.detail or {}).get("statistics_step_id") == step.id:
            db.delete(metric)
    db.flush()
    for result in results:
        artifact = artifacts[result.source_file]
        excluded = result.excluded_counts.model_dump()
        for item in result.metrics:
            db.add(
                Metric(
                    run_id=run.id,
                    name=_metric_name(step, result.source_file, item),
                    value=item.value,
                    unit="ns",
                    sample_count=result.sample_count,
                    detail={
                        "statistics_step_id": step.id,
                        "source_artifact_id": artifact.id,
                        "source_file": result.source_file,
                        "metric_key": item.key,
                        "metric_label": item.label,
                        "script_filename": script_detail["name"],
                        "script_checksum": script_detail["checksum"],
                        "max_latency_ns": max_latency_ns,
                        "excluded_counts": excluded,
                    },
                )
            )
    db.flush()


async def _upload(sftp: typing.Any, remote_path: str, source: Path) -> None:
    temporary = f"{remote_path}.openslt-{uuid4().hex}.tmp"
    try:
        await sftp.put(str(source), temporary)
        await sftp.posix_rename(temporary, remote_path)
    finally:
        with suppress(Exception):
            await sftp.remove(temporary)


async def execute_statistics_node(
    db: Session,
    run: TestRun,
    step: RunStep,
    node: ScenarioWorkflowNode,
    run_resources: dict[str, Resource],
) -> dict[str, typing.Any]:
    resource = run_resources.get("parser")
    if not resource:
        raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少解析工具", 409)
    config = typing.cast(
        StatisticsConfig, parse_node_config(node.node_type, step.config_snapshot or node.config or {})
    )
    inputs = require_statistics_selection(db, run, step)
    try:
        script_detail = await statistics_script_service.read(resource, config.script_filename)
    except StatisticsScriptError as exc:
        raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
    if not script_detail["executable"]:
        raise WorkflowError("STATISTICS_SCRIPT_NOT_EXECUTABLE", "统计脚本没有可执行权限", 409)
    if script_detail["checksum"] != config.script_checksum:
        raise WorkflowError("STATISTICS_SCRIPT_CHANGED", "统计脚本已发生变化，请重新发布工作流", 409)

    remote_workdir = posixpath.join(
        resource.remote_path.rstrip("/"),
        ".openslt-runs",
        f"r{run.id}-s{step.id}-statistics-a{step.retry_count}-{uuid4().hex[:8]}",
    )
    attempts: list[dict[str, typing.Any]] = []
    parsed_results: list[StatisticsScriptOutput] = []
    connection = None
    sftp = None
    started_at = beijing_now()
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        sftp = await connection.start_sftp_client()
        await sftp.makedirs(remote_workdir, exist_ok=True)
        for artifact in inputs:
            remote_csv = posixpath.join(remote_workdir, artifact.name)
            await _upload(sftp, remote_csv, Path(artifact.path))
            command = " ".join(
                (
                    shlex.quote(str(script_detail["path"])),
                    shlex.quote(remote_csv),
                    str(config.max_latency_ns),
                )
            )
            result = await connection.run(command, check=False, timeout=300)
            stdout = str(result.stdout or "")
            stderr = str(result.stderr or "")
            attempt = {
                "artifact_id": artifact.id,
                "source_file": artifact.name,
                "command": command,
                "exit_code": result.exit_status,
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
                "status": "failed",
            }
            attempts.append(attempt)
            if result.exit_status != 0:
                raise WorkflowError(
                    "STATISTICS_SCRIPT_FAILED",
                    f"统计 {artifact.name} 失败（退出码 {result.exit_status}）",
                    409,
                )
            if len(stdout.encode("utf-8")) > MAX_STATISTICS_OUTPUT_BYTES:
                raise WorkflowError("STATISTICS_OUTPUT_TOO_LARGE", "统计脚本输出不能超过 1 MiB", 409)
            try:
                parsed = StatisticsScriptOutput.model_validate_json(stdout)
            except ValidationError as exc:
                raise WorkflowError(
                    "STATISTICS_OUTPUT_INVALID", f"统计 {artifact.name} 的 JSON 输出不合法：{exc.errors()[0]['msg']}", 409
                ) from exc
            if parsed.source_file != artifact.name:
                raise WorkflowError(
                    "STATISTICS_OUTPUT_FILE_MISMATCH", f"统计脚本返回的 source_file 与 {artifact.name} 不一致", 409
                )
            attempt["status"] = "succeeded"
            attempt["result"] = parsed.model_dump()
            parsed_results.append(parsed)
    except WorkflowError:
        step.result_summary = {
            **(step.result_summary or {}),
            "statistics_script": {
                "filename": script_detail["name"],
                "checksum": script_detail["checksum"],
            },
            "statistics_attempts": attempts,
            "remote_workdir": remote_workdir,
        }
        db.flush()
        raise
    except Exception as exc:
        step.result_summary = {
            **(step.result_summary or {}),
            "statistics_script": {
                "filename": script_detail["name"],
                "checksum": script_detail["checksum"],
            },
            "statistics_attempts": attempts,
            "remote_workdir": remote_workdir,
        }
        db.flush()
        raise WorkflowError("STATISTICS_EXECUTION_FAILED", f"数据统计执行失败：{exc}", 409) from exc
    finally:
        if sftp:
            with suppress(Exception):
                sftp.exit()
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()

    by_name = {item.name: item for item in inputs}
    duration_ms = int((beijing_now() - started_at).total_seconds() * 1000)
    consolidated = {
        "schema_version": 1,
        "script": {
            "filename": script_detail["name"],
            "checksum": script_detail["checksum"],
        },
        "max_latency_ns": config.max_latency_ns,
        "inputs": [
            {"artifact_id": item.id, "filename": item.name, "checksum": item.checksum}
            for item in inputs
        ],
        "results": [item.model_dump() for item in parsed_results],
        "duration_ms": duration_ms,
    }
    result_artifact = _register_result_artifact(db, run, step, consolidated)
    _replace_metrics(db, run, step, by_name, parsed_results, script_detail, config.max_latency_ns)
    return {
        **(step.result_summary or {}),
        "statistics_script": consolidated["script"],
        "max_latency_ns": config.max_latency_ns,
        "statistics_attempts": attempts,
        "statistics_results": consolidated["results"],
        "statistics_artifact_id": result_artifact.id,
        "remote_workdir": remote_workdir,
        "duration_ms": duration_ms,
    }
