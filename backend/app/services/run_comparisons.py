from __future__ import annotations

import hashlib
import json
import math
import typing
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Metric, RunComparison, RunStep, TestRun


class RunComparisonError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _source_file(detail: dict[str, typing.Any], metric: Metric) -> str:
    value = str(detail.get("source_file") or "").strip()
    if value:
        return PurePosixPath(value.replace("\\", "/")).name
    path = str(detail.get("source_path") or "").strip()
    return PurePosixPath(path.replace("\\", "/")).name if path else metric.name


def _latest_analysis_reference(step: RunStep) -> dict[str, typing.Any]:
    summary = step.result_summary or {}
    analysis_no = summary.get("statistics_latest_success_analysis_no")
    record = next(
        (
            item for item in reversed(summary.get("statistics_analyses") or [])
            if isinstance(item, dict) and item.get("analysis_no") == analysis_no
        ),
        {},
    )
    script = record.get("script") if isinstance(record.get("script"), dict) else {}
    return {
        "step_id": step.id,
        "step_code": step.code,
        "step_name": step.name,
        "analysis_no": analysis_no if isinstance(analysis_no, int) else None,
        "artifact_id": record.get("artifact_id"),
        "artifact_checksum": record.get("artifact_checksum"),
        "script_filename": script.get("filename") or "",
        "script_checksum": script.get("checksum") or "",
        "max_latency_ns": record.get("max_latency_ns"),
    }


def metric_snapshot(run: TestRun) -> list[dict[str, typing.Any]]:
    steps = {step.id: step for step in run.steps}
    rows: list[dict[str, typing.Any]] = []
    identities: dict[str, int] = {}
    for metric in sorted(run.metrics, key=lambda item: item.id):
        detail = metric.detail or {}
        step_id = detail.get("statistics_step_id")
        step = steps.get(step_id) if isinstance(step_id, int) else None
        step_code = step.code if step else "legacy"
        source_file = _source_file(detail, metric)
        metric_key = str(detail.get("metric_key") or metric.name)
        identity = "\x1f".join((step_code, source_file, metric_key))
        identities[identity] = identities.get(identity, 0) + 1
        if identities[identity] > 1:
            identity = f"{identity}\x1f{identities[identity]}"
        summary = step.result_summary or {} if step else {}
        rows.append({
            "key": identity,
            "step_code": step_code,
            "step_name": step.name if step else "历史指标",
            "source_file": source_file,
            "metric_key": metric_key,
            "metric_label": str(detail.get("metric_label") or metric.name),
            "value": float(metric.value),
            "unit": metric.unit,
            "sample_count": metric.sample_count,
            "analysis_no": summary.get("statistics_latest_success_analysis_no"),
            "script_filename": str(detail.get("script_filename") or ""),
            "script_checksum": str(detail.get("script_checksum") or ""),
            "max_latency_ns": detail.get("max_latency_ns"),
        })
    return rows


def analysis_references(run: TestRun, metrics: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
    step_codes = {str(item.get("step_code") or "") for item in metrics}
    return [
        _latest_analysis_reference(step)
        for step in run.steps
        if step.node_type == "data_statistics" and step.code in step_codes
    ]


def metrics_checksum(metrics: list[dict[str, typing.Any]]) -> str:
    canonical = json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _comparison_rows(
    target_metrics: list[dict[str, typing.Any]],
    baseline_metrics: list[dict[str, typing.Any]],
) -> tuple[list[dict[str, typing.Any]], int, list[str]]:
    target_by_key = {str(item["key"]): item for item in target_metrics}
    baseline_by_key = {str(item["key"]): item for item in baseline_metrics}
    rows: list[dict[str, typing.Any]] = []
    matched = 0
    config_warnings: set[str] = set()
    ordered_keys = list(target_by_key)
    ordered_keys.extend(key for key in baseline_by_key if key not in target_by_key)
    for key in ordered_keys:
        target = target_by_key.get(key)
        baseline = baseline_by_key.get(key)
        source = target or baseline or {}
        target_value = float(target["value"]) if target else None
        baseline_value = float(baseline["value"]) if baseline else None
        delta: typing.Optional[float] = None
        percentage: typing.Optional[float] = None
        if target is None:
            assessment = "missing"
        elif baseline is None:
            assessment = "added"
        elif target.get("unit") != baseline.get("unit"):
            assessment = "incompatible"
            config_warnings.add("部分同名指标的单位不一致")
        elif not target.get("script_checksum") or not baseline.get("script_checksum"):
            assessment = "incompatible"
            config_warnings.add("部分指标缺少统计脚本 checksum")
        elif target.get("script_checksum") != baseline.get("script_checksum"):
            assessment = "incompatible"
            config_warnings.add("统计脚本 checksum 不一致")
        elif target.get("max_latency_ns") is None or baseline.get("max_latency_ns") is None:
            assessment = "incompatible"
            config_warnings.add("部分指标缺少异常大值上限")
        elif target.get("max_latency_ns") != baseline.get("max_latency_ns"):
            assessment = "incompatible"
            config_warnings.add("异常大值上限不一致")
        else:
            matched += 1
            delta = target_value - baseline_value
            if baseline_value != 0:
                percentage = delta / abs(baseline_value) * 100
            elif target_value == 0:
                percentage = 0.0
            if math.isclose(delta, 0.0, rel_tol=1e-12, abs_tol=1e-12):
                assessment = "stable"
            elif delta < 0:
                assessment = "improved"
            else:
                assessment = "regressed"
        rows.append({
            "key": key,
            "step_code": str(source.get("step_code") or ""),
            "step_name": str(source.get("step_name") or ""),
            "source_file": str(source.get("source_file") or ""),
            "metric_key": str(source.get("metric_key") or ""),
            "metric_label": str(source.get("metric_label") or ""),
            "unit": str((target or baseline or {}).get("unit") or ""),
            "baseline_value": baseline_value,
            "target_value": target_value,
            "absolute_delta": delta,
            "percentage_delta": percentage,
            "assessment": assessment,
        })
    return rows, matched, sorted(config_warnings)


def compare_runs(
    target: TestRun,
    baseline: TestRun,
) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]], list[dict[str, typing.Any]], bool, list[str], int]:
    target_metrics = metric_snapshot(target)
    baseline_metrics = metric_snapshot(baseline)
    if not target_metrics:
        raise RunComparisonError("TARGET_METRICS_REQUIRED", "当前运行尚无可比较的统计指标")
    if not baseline_metrics:
        raise RunComparisonError("BASELINE_METRICS_REQUIRED", "基线运行没有可比较的统计指标")
    rows, matched, warnings = _comparison_rows(target_metrics, baseline_metrics)
    compatible = True
    if target.scenario_id != baseline.scenario_id:
        raise RunComparisonError("BASELINE_SCENARIO_MISMATCH", "只能比较同一场景的运行", 400)
    if target.workflow_version_id != baseline.workflow_version_id:
        warnings.insert(0, "工作流版本不一致")
        compatible = False
    if set(target.resource_ids or []) != set(baseline.resource_ids or []):
        warnings.append("运行使用的资源集合不一致")
    incompatible_rows = any(item["assessment"] == "incompatible" for item in rows)
    if incompatible_rows:
        compatible = False
    if not matched:
        warnings.append("没有配置一致的同名指标")
        compatible = False
    missing_count = sum(item["assessment"] == "missing" for item in rows)
    added_count = sum(item["assessment"] == "added" for item in rows)
    if missing_count:
        warnings.append(f"当前运行缺少 {missing_count} 个基线指标")
    if added_count:
        warnings.append(f"当前运行新增 {added_count} 个指标")
    return target_metrics, baseline_metrics, rows, compatible, warnings, matched


def comparison_candidates(db: Session, target: TestRun) -> list[dict[str, typing.Any]]:
    candidates = list(db.scalars(
        select(TestRun)
        .where(
            TestRun.scenario_id == target.scenario_id,
            TestRun.id != target.id,
            TestRun.status == "completed",
        )
        .options(
            selectinload(TestRun.steps),
            selectinload(TestRun.metrics),
            selectinload(TestRun.verdict),
        )
        .order_by(TestRun.finished_at.desc(), TestRun.id.desc())
        .limit(50)
    ).unique().all())
    result: list[dict[str, typing.Any]] = []
    recommendation_assigned = False
    for candidate in candidates:
        if not candidate.metrics:
            continue
        try:
            target_metrics, baseline_metrics, _rows, compatible, warnings, matched = compare_runs(target, candidate)
        except RunComparisonError:
            continue
        passed = bool(candidate.verdict and candidate.verdict.final_result == "passed")
        recommended = compatible and passed and not recommendation_assigned
        if recommended:
            recommendation_assigned = True
        result.append({
            "run_id": candidate.id,
            "run_number": candidate.run_number,
            "finished_at": candidate.finished_at,
            "verdict": candidate.verdict.final_result if candidate.verdict else None,
            "workflow_version_id": candidate.workflow_version_id,
            "compatible": compatible,
            "warnings": warnings,
            "matched_metric_count": matched,
            "metric_count": max(len(target_metrics), len(baseline_metrics)),
            "recommended": recommended,
        })
    return result


def save_comparison(
    db: Session,
    target: TestRun,
    baseline: TestRun,
    actor_id: int,
) -> RunComparison:
    if target.id == baseline.id:
        raise RunComparisonError("BASELINE_SELF_REFERENCE", "不能将当前运行作为自身基线", 400)
    if baseline.status != "completed":
        raise RunComparisonError("BASELINE_NOT_COMPLETED", "基线运行必须已经完成")
    db.scalar(select(TestRun.id).where(TestRun.id == target.id).with_for_update())
    target_metrics, baseline_metrics, rows, compatible, warnings, _matched = compare_runs(target, baseline)
    comparison = db.scalar(select(RunComparison).where(RunComparison.run_id == target.id))
    if comparison is None:
        comparison = RunComparison(run_id=target.id, created_by=actor_id)
        db.add(comparison)
    comparison.baseline_run_id = baseline.id
    comparison.target_run_number = target.run_number
    comparison.baseline_run_number = baseline.run_number
    comparison.target_metrics_checksum = metrics_checksum(target_metrics)
    comparison.baseline_metrics_checksum = metrics_checksum(baseline_metrics)
    comparison.target_metrics_snapshot = target_metrics
    comparison.baseline_metrics_snapshot = baseline_metrics
    comparison.target_analysis_refs = analysis_references(target, target_metrics)
    comparison.baseline_analysis_refs = analysis_references(baseline, baseline_metrics)
    comparison.comparison_rows = rows
    comparison.warnings = warnings
    comparison.is_compatible = compatible
    db.flush()
    return comparison


def comparison_payload(db: Session, comparison: RunComparison) -> dict[str, typing.Any]:
    target = db.scalar(
        select(TestRun)
        .where(TestRun.id == comparison.run_id)
        .options(selectinload(TestRun.steps), selectinload(TestRun.metrics))
    )
    baseline = None
    if comparison.baseline_run_id is not None:
        baseline = db.scalar(
            select(TestRun)
            .where(TestRun.id == comparison.baseline_run_id)
            .options(selectinload(TestRun.steps), selectinload(TestRun.metrics))
        )
    target_stale = bool(
        target and metrics_checksum(metric_snapshot(target)) != comparison.target_metrics_checksum
    )
    baseline_changed = bool(
        baseline and metrics_checksum(metric_snapshot(baseline)) != comparison.baseline_metrics_checksum
    )
    return {
        "id": comparison.id,
        "run_id": comparison.run_id,
        "baseline_run_id": comparison.baseline_run_id,
        "target_run_number": comparison.target_run_number,
        "baseline_run_number": comparison.baseline_run_number,
        "target_analysis_refs": comparison.target_analysis_refs or [],
        "baseline_analysis_refs": comparison.baseline_analysis_refs or [],
        "rows": comparison.comparison_rows or [],
        "warnings": comparison.warnings or [],
        "compatible": comparison.is_compatible,
        "target_metrics_stale": target_stale,
        "baseline_metrics_changed": baseline_changed,
        "created_by": comparison.created_by,
        "created_at": comparison.created_at,
        "updated_at": comparison.updated_at,
    }
