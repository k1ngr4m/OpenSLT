from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import shlex
import typing
from copy import deepcopy
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import asyncssh
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing_extensions import Literal

from app.core.config import settings
from app.core.time import beijing_now, from_unix_timestamp
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
    artifacts = list(db.scalars(select(Artifact).where(Artifact.id.in_(ids))).all())
    by_id = {item.id: item for item in artifacts}
    ordered: list[Artifact] = []
    for artifact_id in ids:
        artifact = by_id.get(artifact_id)
        if (
            artifact is None
            or artifact.run_id != run.id
            or artifact.artifact_type != "parsed_csv"
            or not artifact.is_immutable
        ):
            raise WorkflowError(
                "STATISTICS_INPUT_INVALID", "历史统计输入必须是本次运行归档的不可变 CSV", 409
            )
        path = Path(artifact.path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.checksum:
            raise WorkflowError(
                "STATISTICS_INPUT_CHANGED", f"统计输入 {artifact.name} 已丢失或校验失败", 409
            )
        ordered.append(artifact)
    return ordered


def _remote_csv_detail(
    relative_path: str,
    filename: str,
    source: str,
    attrs: typing.Any,
) -> dict[str, typing.Any]:
    return {
        "relative_path": relative_path,
        "filename": filename,
        "source": source,
        "size": int(attrs.size or 0),
        "modified_at": from_unix_timestamp(attrs.mtime or 0).isoformat(),
    }


async def list_statistics_csv_files(
    resource: Resource,
    run: TestRun,
    step: RunStep,
) -> dict[str, typing.Any]:
    if resource.is_deleted or not resource.is_enabled or resource.resource_type != "parser":
        raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少已启用的解析工具", 409)
    configured_directory = resource.remote_path.strip()
    if not configured_directory:
        raise WorkflowError("STATISTICS_SOURCE_PATH_REQUIRED", "解析工具远端路径不能为空", 409)
    directory = posixpath.normpath(configured_directory)

    parser_step = next(
        (
            item
            for item in sorted(run.steps, key=lambda candidate: candidate.position, reverse=True)
            if item.node_type == "parser_parse"
            and item.status == "succeeded"
            and item.position < step.position
        ),
        None,
    )
    if parser_step is None:
        raise WorkflowError(
            "STATISTICS_PARSER_RESULT_REQUIRED",
            "当前统计节点之前没有成功的数据解析结果",
            409,
        )
    summary = parser_step.result_summary or {}
    raw_workdir = summary.get("remote_workdir")
    raw_output_files = summary.get("output_files")
    if not isinstance(raw_workdir, str) or not raw_workdir.strip():
        raise WorkflowError(
            "STATISTICS_PARSER_RESULT_INVALID",
            "最近的数据解析结果缺少有效的远端目录",
            409,
        )
    remote_workdir = posixpath.normpath(raw_workdir.strip())
    resource_directory = posixpath.normpath(directory)
    if (
        remote_workdir != resource_directory
        and not remote_workdir.startswith(f"{resource_directory}/.openslt-runs/")
    ):
        raise WorkflowError(
            "STATISTICS_PARSER_RESULT_INVALID",
            "最近的数据解析结果目录不属于当前解析资源",
            409,
        )
    if not isinstance(raw_output_files, list):
        raise WorkflowError(
            "STATISTICS_PARSER_RESULT_INVALID",
            "最近的数据解析结果缺少有效的 CSV 输出清单",
            409,
        )
    output_files = {
        name
        for name in raw_output_files
        if isinstance(name, str)
        and name == posixpath.basename(name)
        and name.lower().endswith(".csv")
    }
    if not output_files:
        raise WorkflowError(
            "STATISTICS_PARSER_RESULT_INVALID",
            "最近的数据解析结果没有可用的 CSV 输出",
            409,
        )

    rows: list[dict[str, typing.Any]] = []
    connection = None
    sftp = None
    try:
        connection = await asyncssh.connect(**_ssh_options(resource))
        sftp = await connection.start_sftp_client()
        async for entry in sftp.scandir(remote_workdir):
            if (
                entry.filename in output_files
                and entry.filename.lower().endswith(".csv")
                and entry.attrs.type == asyncssh.FILEXFER_TYPE_REGULAR
            ):
                rows.append(_remote_csv_detail(
                    entry.filename, entry.filename, "current_run", entry.attrs
                ))
    except WorkflowError:
        raise
    except (asyncssh.Error, OSError) as exc:
        raise WorkflowError(
            "STATISTICS_SOURCE_LIST_FAILED", f"读取远端统计 CSV 失败：{exc}", 409
        ) from exc
    finally:
        if sftp:
            with suppress(Exception):
                sftp.exit()
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()
    rows.sort(key=lambda item: item["relative_path"])
    return {"directory": remote_workdir, "files": rows}


async def select_statistics_inputs(
    db: Session,
    run: TestRun,
    step: RunStep,
    resource: Resource,
    relative_paths: list[str],
    actor_id: int,
) -> dict[str, typing.Any]:
    return await update_statistics_runtime_config(
        db,
        run,
        step,
        resource,
        relative_paths,
        int((step.config_snapshot or {}).get("max_latency_ns") or 999999999),
        actor_id,
    )


def _statistics_input_snapshot(item: typing.Mapping[str, typing.Any]) -> dict[str, typing.Any]:
    return {
        key: item[key]
        for key in (
            "artifact_id", "relative_path", "source_path", "filename", "source", "size", "modified_at", "checksum",
        )
        if key in item
    }


async def update_statistics_runtime_config(
    db: Session,
    run: TestRun,
    step: RunStep,
    resource: Resource,
    relative_paths: list[str],
    max_latency_ns: int,
    actor_id: int,
) -> dict[str, typing.Any]:
    if not relative_paths:
        raise WorkflowError("STATISTICS_INPUTS_REQUIRED", "请至少选择一个统计 CSV", 409)
    if len(relative_paths) != len(set(relative_paths)):
        raise WorkflowError("STATISTICS_INPUTS_DUPLICATE", "统计输入不能重复", 400)
    if max_latency_ns < 1:
        raise WorkflowError("STATISTICS_MAX_LATENCY_INVALID", "异常大值上限必须为正整数", 400)
    listing = await list_statistics_csv_files(resource, run, step)
    available = {item["relative_path"]: item for item in listing["files"]}
    try:
        inputs = [dict(available[path]) for path in relative_paths]
    except (KeyError, TypeError) as exc:
        raise WorkflowError(
            "STATISTICS_INPUT_INVALID", "只能选择当前列表中的远端 CSV", 409
        ) from exc
    summary = dict(step.result_summary or {})
    existing_selection = summary.get("statistics_selection") or {}
    existing_inputs = existing_selection.get("inputs") if isinstance(existing_selection, dict) else None
    existing_max_latency_ns = (step.config_snapshot or {}).get("max_latency_ns")
    changed = existing_inputs != inputs or existing_max_latency_ns != max_latency_ns
    if changed:
        selected_at = beijing_now()
        selection = {
            "inputs": inputs,
            "selected_by": actor_id,
            "selected_at": selected_at.isoformat(),
        }
        summary["statistics_selection"] = selection
        summary["statistics_config_revision"] = int(
            summary.get("statistics_config_revision") or 0
        ) + 1
        summary.setdefault("statistics_analyses", [])
        step.config_snapshot = {
            **(step.config_snapshot or {}),
            "max_latency_ns": max_latency_ns,
        }
        step.result_summary = summary
    else:
        selection = typing.cast(typing.Dict[str, typing.Any], existing_selection)
    db.flush()
    return {
        **selection,
        "max_latency_ns": int((step.config_snapshot or {}).get("max_latency_ns") or max_latency_ns),
        "statistics_config_revision": int(summary.get("statistics_config_revision") or 0),
        "changed": changed,
    }


def reserve_statistics_analysis(db: Session, run: TestRun, step: RunStep) -> int:
    """为一次统计执行预留单调分析号；Task 2 以此轻量索引定位不可变产物。"""
    summary = dict(step.result_summary or {})
    history = [dict(item) for item in summary.get("statistics_analyses") or [] if isinstance(item, dict)]
    analysis_no = max(
        (int(item.get("analysis_no") or 0) for item in history), default=0
    ) + 1
    now = beijing_now().isoformat()
    config = step.config_snapshot if isinstance(step.config_snapshot, dict) else {}
    selection = summary.get("statistics_selection") or {}
    raw_inputs = selection.get("inputs") if isinstance(selection, dict) else []
    raw_inputs = raw_inputs if isinstance(raw_inputs, list) else []
    raw_max_latency = config.get("max_latency_ns")
    history.append(
        {
            "analysis_no": analysis_no,
            "status": "running",
            "config_revision": int(summary.get("statistics_config_revision") or 0),
            # 预留必须早于校验：失败分析仍需保留当时可序列化的原始输入/配置快照。
            "inputs": [
                _statistics_input_snapshot(item)
                for item in raw_inputs
                if isinstance(item, dict)
            ],
            "max_latency_ns": raw_max_latency if isinstance(raw_max_latency, int) else None,
            "script": {
                "filename": str(config.get("script_filename") or ""),
                "checksum": str(config.get("script_checksum") or ""),
            },
            "reserved_at": now,
            "started_at": now,
        }
    )
    step.result_summary = {
        **summary,
        "statistics_analyses": history,
        "statistics_active_analysis_no": analysis_no,
        "statistics_next_analysis_no": analysis_no + 1,
    }
    db.flush()
    return analysis_no


def finish_statistics_analysis(
    db: Session,
    run: TestRun,
    step: RunStep,
    *,
    status: str,
    error_code: typing.Optional[str] = None,
    error_message: typing.Optional[str] = None,
) -> None:
    summary = dict(step.result_summary or {})
    active = summary.get("statistics_active_analysis_no")
    if not isinstance(active, int):
        return
    history = [dict(item) for item in summary.get("statistics_analyses") or [] if isinstance(item, dict)]
    record = next((item for item in reversed(history) if item.get("analysis_no") == active), None)
    if record is None:
        return
    # 执行服务已经将完整成功/失败结果封存后，任务恢复路径不应覆盖该不可变结论。
    if record.get("artifact_id"):
        return
    if status == "failed":
        raw_started_at = record.get("started_at")
        try:
            started_at = (
                datetime.fromisoformat(raw_started_at)
                if isinstance(raw_started_at, str)
                else step.started_at or beijing_now()
            )
        except ValueError:
            started_at = step.started_at or beijing_now()
        code = error_code or "WORKFLOW_EXECUTION_FAILED"
        _finalize_statistics_analysis(
            db,
            run,
            step,
            status="failed",
            script=dict(record.get("script") or {}),
            inputs=[
                dict(item)
                for item in record.get("inputs") or []
                if isinstance(item, dict)
            ],
            attempts=[],
            started_at=started_at,
            error=WorkflowError(code, error_message or code, 409),
        )
        return
    record["status"] = status
    record["finished_at"] = beijing_now().isoformat()
    if error_code:
        record["error_code"] = error_code
    if status == "succeeded":
        summary["statistics_latest_success_analysis_no"] = active
        summary["statistics_latest_success_revision"] = record["config_revision"]
    summary["statistics_analyses"] = history
    step.result_summary = summary
    db.flush()


def validate_statistics_completion_freshness(step: RunStep) -> None:
    summary = step.result_summary or {}
    # 只有从未使用运行时配置修订的历史运行保持原有完成行为。
    if (
        "statistics_analyses" not in summary
        and "statistics_config_revision" not in summary
    ):
        return
    revision = int(summary.get("statistics_config_revision") or 0)
    latest_no = summary.get("statistics_latest_success_analysis_no")
    history = summary.get("statistics_analyses") or []
    latest = next(
        (
            item for item in reversed(history)
            if isinstance(item, dict) and item.get("analysis_no") == latest_no
        ),
        None,
    )
    if (
        summary.get("statistics_latest_success_revision") != revision
        or not isinstance(latest, dict)
        or latest.get("status") != "succeeded"
        or latest.get("config_revision") != revision
    ):
        raise WorkflowError(
            "STATISTICS_ANALYSIS_STALE",
            "当前统计配置尚未完成对应的分析，不能完成节点",
            409,
        )


def require_statistics_selection(
    db: Session, run: TestRun, step: RunStep
) -> list[dict[str, typing.Any]]:
    selection = (step.result_summary or {}).get("statistics_selection") or {}
    raw_inputs = selection.get("inputs") or []
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise WorkflowError("STATISTICS_INPUTS_REQUIRED", "请重新选择统计输入", 409)
    if all(isinstance(item, dict) and isinstance(item.get("relative_path"), str) for item in raw_inputs):
        relative_paths = [str(item["relative_path"]) for item in raw_inputs]
        if len(relative_paths) != len(set(relative_paths)):
            raise WorkflowError("STATISTICS_INPUTS_DUPLICATE", "统计输入不能重复", 400)
        return [dict(item) for item in raw_inputs]
    artifact_ids = [item.get("artifact_id") for item in raw_inputs if isinstance(item, dict)]
    if len(artifact_ids) != len(raw_inputs) or any(not isinstance(item, int) for item in artifact_ids):
        raise WorkflowError("STATISTICS_INPUTS_REQUIRED", "请重新选择统计输入", 409)
    artifacts = _validated_input_artifacts(
        db, run, step, typing.cast(typing.List[int], artifact_ids)
    )
    return [
        {
            "artifact": artifact,
            "artifact_id": artifact.id,
            "filename": artifact.name,
            "source_path": artifact.name,
            "size": artifact.size,
            "checksum": artifact.checksum,
        }
        for artifact in artifacts
    ]


async def _execution_inputs(
    db: Session,
    run: TestRun,
    step: RunStep,
    resource: Resource,
) -> list[dict[str, typing.Any]]:
    selected = require_statistics_selection(db, run, step)
    if all("artifact" in item for item in selected):
        return selected
    listing = await list_statistics_csv_files(resource, run, step)
    available = {item["relative_path"]: item for item in listing["files"]}
    resolved: list[dict[str, typing.Any]] = []
    for item in selected:
        relative_path = str(item.get("relative_path") or "")
        current = available.get(relative_path)
        if current is None:
            raise WorkflowError(
                "STATISTICS_INPUT_CHANGED", f"远端统计输入 {relative_path} 已不存在", 409
            )
        if (
            current["size"] != item.get("size")
            or current["modified_at"] != item.get("modified_at")
        ):
            raise WorkflowError(
                "STATISTICS_INPUT_CHANGED", f"远端统计输入 {relative_path} 已发生变化", 409
            )
        resolved.append({
            **current,
            "source_path": relative_path,
            "absolute_path": posixpath.join(listing["directory"], relative_path),
        })
    return resolved


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


def _analysis_record(step: RunStep) -> dict[str, typing.Any]:
    summary = step.result_summary or {}
    active = summary.get("statistics_active_analysis_no")
    history = summary.get("statistics_analyses") or []
    if not isinstance(active, int):
        raise WorkflowError("STATISTICS_ANALYSIS_MISSING", "当前统计执行缺少分析编号", 409)
    record = next(
        (item for item in reversed(history) if isinstance(item, dict) and item.get("analysis_no") == active),
        None,
    )
    if not isinstance(record, dict):
        raise WorkflowError("STATISTICS_ANALYSIS_MISSING", "当前统计执行缺少历史预留记录", 409)
    return record


_ANALYSIS_PAYLOAD_REQUIRED_FIELDS = frozenset({
    "schema_version",
    "analysis_no",
    "status",
    "config_revision",
    "inputs",
    "max_latency_ns",
    "script",
    "reserved_at",
    "started_at",
    "finished_at",
    "duration_ms",
    "attempts",
})
_ANALYSIS_PAYLOAD_STABLE_FIELDS = (
    "status",
    "config_revision",
    "inputs",
    "max_latency_ns",
    "script",
    "reserved_at",
    "started_at",
    "attempts",
    "results",
    "error",
)


def _analysis_artifact_corrupt(name: str, message: str) -> WorkflowError:
    return WorkflowError(
        "STATISTICS_ANALYSIS_ARTIFACT_CORRUPT",
        f"分析历史产物 {name}{message}",
        409,
    )


def _validate_terminal_analysis_payload(
    value: typing.Any,
    *,
    analysis_no: int,
    artifact_name: str,
    expected: typing.Optional[dict[str, typing.Any]] = None,
) -> dict[str, typing.Any]:
    if not isinstance(value, dict) or not _ANALYSIS_PAYLOAD_REQUIRED_FIELDS.issubset(value):
        raise _analysis_artifact_corrupt(artifact_name, " 内容不完整")
    status = value.get("status")
    duration_ms = value.get("duration_ms")
    max_latency_ns = value.get("max_latency_ns")
    if (
        value.get("schema_version") != 1
        or value.get("analysis_no") != analysis_no
        or status not in {"succeeded", "failed"}
        or not isinstance(value.get("config_revision"), int)
        or isinstance(value.get("config_revision"), bool)
        or int(value["config_revision"]) < 0
        or not isinstance(value.get("inputs"), list)
        or (
            max_latency_ns is not None
            and (
                not isinstance(max_latency_ns, int)
                or isinstance(max_latency_ns, bool)
                or max_latency_ns < 1
            )
        )
        or not isinstance(value.get("script"), dict)
        or not isinstance(value.get("reserved_at"), str)
        or not value["reserved_at"]
        or not isinstance(value.get("started_at"), str)
        or not value["started_at"]
        or not isinstance(value.get("finished_at"), str)
        or not value["finished_at"]
        or not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms < 0
        or not isinstance(value.get("attempts"), list)
        or (status == "succeeded" and not isinstance(value.get("results"), list))
        or (
            status == "failed"
            and (
                not isinstance(value.get("error"), dict)
                or not isinstance(value["error"].get("code"), str)
                or not value["error"]["code"]
                or not isinstance(value["error"].get("message"), str)
            )
        )
    ):
        raise _analysis_artifact_corrupt(artifact_name, " 内容不完整或字段非法")
    try:
        for field in ("reserved_at", "started_at", "finished_at"):
            datetime.fromisoformat(value[field])
    except ValueError as exc:
        raise _analysis_artifact_corrupt(artifact_name, " 时间字段非法") from exc
    if expected is not None and any(
        value.get(key) != expected.get(key)
        for key in _ANALYSIS_PAYLOAD_STABLE_FIELDS
    ):
        raise _analysis_artifact_corrupt(artifact_name, " 与当前分析终态冲突")
    return dict(value)


def _read_analysis_artifact_payload(
    artifact: Artifact,
    *,
    target: Path,
    analysis_no: int,
    expected: dict[str, typing.Any],
) -> dict[str, typing.Any]:
    path = Path(artifact.path)
    if (
        artifact.run_id != expected["_run_id"]
        or artifact.step_id != expected["_step_id"]
        or artifact.artifact_type != "statistics_analysis_json"
        or artifact.name != target.name
        or not artifact.is_immutable
        or path.is_symlink()
        or not path.is_file()
        or path.resolve() != target.resolve()
    ):
        raise _analysis_artifact_corrupt(target.name, " 元数据已损坏")
    data = path.read_bytes()
    if (
        len(data) != artifact.size
        or hashlib.sha256(data).hexdigest() != artifact.checksum
    ):
        raise _analysis_artifact_corrupt(target.name, " 校验失败")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _analysis_artifact_corrupt(target.name, " 不是合法 JSON") from exc
    return _validate_terminal_analysis_payload(
        payload,
        analysis_no=analysis_no,
        artifact_name=target.name,
        expected={key: value for key, value in expected.items() if not key.startswith("_")},
    )


def _read_orphaned_analysis_payload(
    target: Path,
    *,
    analysis_no: int,
    expected: dict[str, typing.Any],
) -> tuple[bytes, dict[str, typing.Any]]:
    if target.is_symlink() or not target.is_file():
        raise _analysis_artifact_corrupt(target.name, " 不是普通文件")
    data = target.read_bytes()
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _analysis_artifact_corrupt(target.name, " 不是合法 JSON") from exc
    return data, _validate_terminal_analysis_payload(
        payload,
        analysis_no=analysis_no,
        artifact_name=target.name,
        expected=expected,
    )


def _register_analysis_artifact(
    db: Session,
    run: TestRun,
    step: RunStep,
    analysis_no: int,
    payload: dict[str, typing.Any],
) -> Artifact:
    """按分析号一次性封存；恢复时只复用既有不可变产物，绝不覆写。"""
    directory = _artifact_directory(run, step)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"statistics-analysis-v{analysis_no:03d}.json"
    idempotency_key = f"statistics-analysis:{run.id}:{step.id}:{analysis_no}"
    expected_payload = _validate_terminal_analysis_payload(
        payload,
        analysis_no=analysis_no,
        artifact_name=target.name,
    )
    expected_with_identity = {
        **expected_payload,
        "_run_id": run.id,
        "_step_id": step.id,
    }
    artifact = db.scalar(
        select(Artifact).where(Artifact.idempotency_key == idempotency_key)
    )
    if artifact is None:
        artifact = db.scalar(select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.step_id == step.id,
            Artifact.artifact_type == "statistics_analysis_json",
            Artifact.name == target.name,
        ))
    if artifact is not None:
        _read_analysis_artifact_payload(
            artifact,
            target=target,
            analysis_no=analysis_no,
            expected=expected_with_identity,
        )
        if artifact.idempotency_key not in {None, idempotency_key}:
            raise _analysis_artifact_corrupt(target.name, " 幂等键冲突")
        if artifact.idempotency_key is None:
            try:
                with db.begin_nested():
                    artifact.idempotency_key = idempotency_key
                    db.flush()
            except IntegrityError as exc:
                winner = db.scalar(
                    select(Artifact)
                    .where(Artifact.idempotency_key == idempotency_key)
                    .with_for_update()
                )
                if winner is None:
                    raise _analysis_artifact_corrupt(target.name, " 幂等登记冲突") from exc
                _read_analysis_artifact_payload(
                    winner,
                    target=target,
                    analysis_no=analysis_no,
                    expected=expected_with_identity,
                )
                return winner
        return artifact

    data = json.dumps(expected_payload, ensure_ascii=False, indent=2).encode("utf-8")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            data, _sealed_payload = _read_orphaned_analysis_payload(
                target,
                analysis_no=analysis_no,
                expected=expected_payload,
            )
    finally:
        temporary.unlink(missing_ok=True)
    artifact = Artifact(
        run_id=run.id,
        step_id=step.id,
        artifact_type="statistics_analysis_json",
        name=target.name,
        path=str(target),
        content_type="application/json",
        size=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        is_immutable=True,
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(artifact)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalar(
            select(Artifact)
            .where(Artifact.idempotency_key == idempotency_key)
            .with_for_update()
        )
        if winner is None:
            raise _analysis_artifact_corrupt(target.name, " 幂等登记冲突") from exc
        _read_analysis_artifact_payload(
            winner,
            target=target,
            analysis_no=analysis_no,
            expected=expected_with_identity,
        )
        return winner
    return artifact


def _finalize_statistics_analysis(
    db: Session,
    run: TestRun,
    step: RunStep,
    *,
    status: str,
    script: dict[str, typing.Any],
    inputs: list[dict[str, typing.Any]],
    attempts: list[dict[str, typing.Any]],
    started_at: typing.Any,
    results: typing.Optional[list[dict[str, typing.Any]]] = None,
    error: typing.Optional[WorkflowError] = None,
) -> tuple[dict[str, typing.Any], Artifact]:
    summary = deepcopy(step.result_summary or {})
    active = summary.get("statistics_active_analysis_no")
    record = next(
        (
            item
            for item in reversed(summary.get("statistics_analyses") or [])
            if isinstance(item, dict) and item.get("analysis_no") == active
        ),
        None,
    )
    if not isinstance(record, dict):
        raise WorkflowError(
            "STATISTICS_ANALYSIS_MISSING",
            "当前统计执行缺少历史预留记录",
            409,
        )
    finished_at = beijing_now()
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    artifact_payload: dict[str, typing.Any] = {
        "schema_version": 1,
        "analysis_no": record["analysis_no"],
        "status": status,
        "config_revision": record.get("config_revision"),
        "inputs": record.get("inputs") or [_statistics_input_snapshot(item) for item in inputs],
        "max_latency_ns": record.get("max_latency_ns"),
        "script": script,
        "reserved_at": record.get("reserved_at"),
        "started_at": record.get("started_at") or started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": duration_ms,
        "attempts": attempts,
    }
    if results is not None:
        artifact_payload["results"] = results
    if error is not None:
        artifact_payload["error"] = {"code": error.code, "message": error.message}
    artifact = _register_analysis_artifact(db, run, step, int(record["analysis_no"]), artifact_payload)
    sealed_payload = _read_analysis_artifact_payload(
        artifact,
        target=_artifact_directory(run, step) / f"statistics-analysis-v{int(record['analysis_no']):03d}.json",
        analysis_no=int(record["analysis_no"]),
        expected={
            **artifact_payload,
            "_run_id": run.id,
            "_step_id": step.id,
        },
    )
    record.update({
        "status": sealed_payload["status"],
        "config_revision": sealed_payload["config_revision"],
        "inputs": sealed_payload["inputs"],
        "max_latency_ns": sealed_payload["max_latency_ns"],
        "script": {
            "filename": sealed_payload["script"].get("filename", ""),
            "checksum": sealed_payload["script"].get("checksum", ""),
        },
        "reserved_at": sealed_payload["reserved_at"],
        "started_at": sealed_payload["started_at"],
        "artifact_id": artifact.id,
        "artifact_checksum": artifact.checksum,
        "artifact_size": artifact.size,
        "finished_at": sealed_payload["finished_at"],
        "duration_ms": sealed_payload["duration_ms"],
    })
    sealed_error = sealed_payload.get("error")
    if isinstance(sealed_error, dict):
        record["error_code"] = sealed_error.get("code")
    else:
        record.pop("error_code", None)
    summary["statistics_analyses"] = [
        record if isinstance(item, dict) and item.get("analysis_no") == record["analysis_no"] else item
        for item in (summary.get("statistics_analyses") or [])
    ]
    if sealed_payload["status"] == "succeeded":
        summary["statistics_latest_success_analysis_no"] = record["analysis_no"]
        summary["statistics_latest_success_revision"] = record.get("config_revision")
    step.result_summary = summary
    db.flush()
    return summary, artifact


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
    executions: list[tuple[dict[str, typing.Any], StatisticsScriptOutput]],
    script_detail: dict[str, typing.Any],
    max_latency_ns: int,
) -> None:
    existing = list(db.scalars(select(Metric).where(Metric.run_id == run.id)).all())
    for metric in existing:
        if (metric.detail or {}).get("statistics_step_id") == step.id:
            db.delete(metric)
    db.flush()
    for source, result in executions:
        source_path = str(source["source_path"])
        artifact = source.get("artifact")
        excluded = result.excluded_counts.model_dump()
        for item in result.metrics:
            db.add(
                Metric(
                    run_id=run.id,
                    name=_metric_name(step, source_path, item),
                    value=item.value,
                    unit="ns",
                    sample_count=result.sample_count,
                    detail={
                        "statistics_step_id": step.id,
                        "source_artifact_id": artifact.id if artifact else None,
                        "source_file": result.source_file,
                        "source_path": source_path,
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
    # 直接服务调用也属于一次真实执行；工作流路径已预留时则复用同一记录。
    active_record = None
    with suppress(WorkflowError):
        active_record = _analysis_record(step)
    if not active_record or active_record.get("status") != "running":
        reserve_statistics_analysis(db, run, step)
    raw_config_value = step.config_snapshot or node.config or {}
    raw_config = raw_config_value if isinstance(raw_config_value, dict) else {}
    script = {
        "filename": str(raw_config.get("script_filename") or ""),
        "checksum": str(raw_config.get("script_checksum") or ""),
    }
    inputs: list[dict[str, typing.Any]] = []
    attempts: list[dict[str, typing.Any]] = []
    parsed_results: list[tuple[dict[str, typing.Any], StatisticsScriptOutput]] = []
    connection = None
    sftp = None
    started_at = beijing_now()
    try:
        if not isinstance(raw_config_value, dict):
            raise WorkflowError("STATISTICS_CONFIG_INVALID", "统计运行配置必须是对象", 409)
        try:
            config = typing.cast(StatisticsConfig, parse_node_config(node.node_type, raw_config))
        except WorkflowError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise WorkflowError("STATISTICS_CONFIG_INVALID", f"统计运行配置无效：{exc}", 409) from exc
        script = {"filename": config.script_filename, "checksum": config.script_checksum}
        resource = run_resources.get("parser")
        if not resource:
            raise WorkflowError("PARSER_RESOURCE_REQUIRED", "运行资源缺少解析工具", 409)
        inputs = await _execution_inputs(db, run, step, resource)
        try:
            script_detail = await statistics_script_service.read(resource, config.script_filename)
        except StatisticsScriptError as exc:
            raise WorkflowError(exc.code, exc.message, exc.status_code) from exc
        script = {"filename": script_detail["name"], "checksum": script_detail["checksum"]}
        if not script_detail["executable"]:
            raise WorkflowError("STATISTICS_SCRIPT_NOT_EXECUTABLE", "统计脚本没有可执行权限", 409)
        if script_detail["checksum"] != config.script_checksum:
            raise WorkflowError("STATISTICS_SCRIPT_CHANGED", "统计脚本已发生变化，请重新发布工作流", 409)
        legacy_inputs = any("artifact" in item for item in inputs)
        remote_workdir = posixpath.normpath(resource.remote_path.strip()) if legacy_inputs else ""
        connection = await asyncssh.connect(**_ssh_options(resource))
        sftp = await connection.start_sftp_client()
        if legacy_inputs:
            await sftp.makedirs(remote_workdir, exist_ok=True)
        for source in inputs:
            artifact = source.get("artifact")
            if artifact:
                remote_csv = posixpath.join(remote_workdir, artifact.name)
                await _upload(sftp, remote_csv, Path(artifact.path))
            else:
                remote_csv = str(source["absolute_path"])
            filename = str(source["filename"])
            source_path = str(source["source_path"])
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
                "artifact_id": artifact.id if artifact else None,
                "source_file": filename,
                "source_path": source_path,
                "size": source.get("size"),
                "modified_at": source.get("modified_at"),
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
                    f"统计 {source_path} 失败（退出码 {result.exit_status}）",
                    409,
                )
            if len(stdout.encode("utf-8")) > MAX_STATISTICS_OUTPUT_BYTES:
                raise WorkflowError("STATISTICS_OUTPUT_TOO_LARGE", "统计脚本输出不能超过 1 MiB", 409)
            try:
                parsed = StatisticsScriptOutput.model_validate_json(stdout)
            except ValidationError as exc:
                raise WorkflowError(
                    "STATISTICS_OUTPUT_INVALID", f"统计 {source_path} 的 JSON 输出不合法：{exc.errors()[0]['msg']}", 409
                ) from exc
            if parsed.source_file != filename:
                raise WorkflowError(
                    "STATISTICS_OUTPUT_FILE_MISMATCH", f"统计脚本返回的 source_file 与 {filename} 不一致", 409
                )
            attempt["status"] = "succeeded"
            attempt["result"] = {**parsed.model_dump(), "source_path": source_path}
            parsed_results.append((source, parsed))
    except WorkflowError as exc:
        _finalize_statistics_analysis(
            db, run, step, status="failed", script=script, inputs=inputs,
            attempts=attempts, started_at=started_at, error=exc,
        )
        raise
    except Exception as exc:
        error = WorkflowError("STATISTICS_EXECUTION_FAILED", f"数据统计执行失败：{exc}", 409)
        _finalize_statistics_analysis(
            db, run, step, status="failed", script=script, inputs=inputs,
            attempts=attempts, started_at=started_at, error=error,
        )
        raise error from exc
    finally:
        if sftp:
            with suppress(Exception):
                sftp.exit()
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()

    results = [
        {**result.model_dump(), "source_path": source["source_path"]}
        for source, result in parsed_results
    ]
    # 必须先封存并校验成功产物；封存失败时不允许改变当前最新指标或兼容结果。
    history_summary, result_artifact = _finalize_statistics_analysis(
        db, run, step, status="succeeded", script=script, inputs=inputs,
        attempts=attempts, started_at=started_at, results=results,
    )
    _replace_metrics(db, run, step, parsed_results, script_detail, config.max_latency_ns)
    result_summary = {
        **history_summary,
        "statistics_script": script,
        "max_latency_ns": config.max_latency_ns,
        "statistics_results": results,
        "statistics_artifact_id": result_artifact.id,
        "remote_workdir": remote_workdir or None,
        "duration_ms": history_summary["statistics_analyses"][-1]["duration_ms"],
    }
    step.result_summary = result_summary
    db.flush()
    return step.result_summary
