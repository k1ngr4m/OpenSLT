from __future__ import annotations

import csv
import io
import json
import typing
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import admin_only, get_current_user
from app.api.routes.common import not_found
from app.core.config import settings
from app.core.database import get_db
from app.core.observability import read_event_payload
from app.models import Artifact, AuditLog, LogRecord, User
from app.schemas import AuditOut, LogDetailOut, LogOut, LogSearchPage, LogSummaryOut
from app.services.audit import write_audit
from app.services.reports import SENSITIVE_ARTIFACT_TYPES

router = APIRouter()

@router.get("/logs", response_model=typing.List[LogOut])
def query_logs(log_type: typing.Union[str, None] = None, level: typing.Union[str, None] = None, trace_id: typing.Union[str, None] = None, keyword: typing.Union[str, None] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[LogRecord]:
    query = select(LogRecord)
    if user.role == "visitor": query = query.where(LogRecord.log_type.notin_(["command", "remote_command", "access", "sql", "websocket"]))
    if log_type: query = query.where(LogRecord.log_type == log_type)
    if level: query = query.where(LogRecord.level == level.upper())
    if trace_id: query = query.where(LogRecord.trace_id == trace_id)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at.desc()).limit(1000)).all())


@router.get("/logs/search", response_model=LogSearchPage)
def search_logs(
    group: typing.Union[str, None] = None,
    log_type: typing.Union[str, None] = None,
    level: typing.Union[str, None] = None,
    trace_id: typing.Union[str, None] = None,
    user_id: typing.Union[int, None] = None,
    event: typing.Union[str, None] = None,
    keyword: typing.Union[str, None] = None,
    http_method: typing.Union[str, None] = None,
    http_path: typing.Union[str, None] = None,
    http_status: typing.Union[int, None] = None,
    database_scope: typing.Union[str, None] = None,
    sql_fingerprint: typing.Union[str, None] = None,
    result: typing.Union[str, None] = None,
    min_duration_ms: typing.Union[int, None] = Query(default=None, ge=0),
    time_from: typing.Union[datetime, None] = None,
    time_to: typing.Union[datetime, None] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LogSearchPage:
    query = select(LogRecord)
    if user.role == "visitor":
        query = query.where(
            LogRecord.log_type.notin_(
                ["command", "remote_command", "access", "sql", "websocket"]
            )
        )
    if group == "application":
        query = query.where(LogRecord.log_type.notin_(["access", "sql", "websocket"]))
    elif group in {"access", "sql", "websocket"}:
        query = query.where(LogRecord.log_type == group)
    if log_type:
        query = query.where(LogRecord.log_type == log_type)
    if level:
        query = query.where(LogRecord.level == level.upper())
    if trace_id:
        query = query.where(LogRecord.trace_id == trace_id)
    if user_id is not None:
        query = query.where(LogRecord.user_id == user_id)
    if event:
        query = query.where(LogRecord.event == event)
    if keyword:
        query = query.where(
            or_(LogRecord.event.contains(keyword), LogRecord.message.contains(keyword))
        )
    if http_method:
        query = query.where(LogRecord.http_method == http_method.upper())
    if http_path:
        query = query.where(LogRecord.message.contains(http_path))
    if http_status is not None:
        query = query.where(LogRecord.http_status == http_status)
    if database_scope:
        query = query.where(LogRecord.database_scope == database_scope)
    if sql_fingerprint:
        query = query.where(LogRecord.sql_fingerprint == sql_fingerprint)
    if result:
        query = query.where(LogRecord.result == result)
    if min_duration_ms is not None:
        query = query.where(LogRecord.duration_ms >= min_duration_ms)
    if time_from:
        query = query.where(LogRecord.created_at >= time_from)
    if time_to:
        query = query.where(LogRecord.created_at <= time_to)

    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = list(
        db.scalars(
            query.order_by(LogRecord.created_at.desc(), LogRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return LogSearchPage(
        items=[LogSummaryOut.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/logs/{event_id}", response_model=LogDetailOut)
def log_detail(
    event_id: str,
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> LogDetailOut:
    record = db.scalar(select(LogRecord).where(LogRecord.event_id == event_id))
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOG_NOT_FOUND", "message": "日志不存在或已过保留期"},
        )
    try:
        payload = read_event_payload(record)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=410,
            detail={"code": "LOG_PAYLOAD_UNAVAILABLE", "message": "日志正文不可用"},
        ) from exc
    return LogDetailOut(summary=LogSummaryOut.model_validate(record), payload=payload)


@router.get("/audit-logs", response_model=typing.List[AuditOut])
def list_audit_logs(action: typing.Union[str, None] = None, object_type: typing.Union[str, None] = None, _: User = Depends(admin_only), db: Session = Depends(get_db)) -> typing.List[AuditLog]:
    query = select(AuditLog)
    if action: query = query.where(AuditLog.action == action)
    if object_type: query = query.where(AuditLog.object_type == object_type)
    return list(db.scalars(query.order_by(AuditLog.created_at.desc()).limit(2000)).all())


@router.get("/audit-logs/export")
def export_audit_logs(request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> StreamingResponse:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all(); output = io.StringIO(); writer = csv.writer(output); writer.writerow(["id", "time_beijing", "actor_id", "action", "object_type", "object_id", "result", "trace_id"])
    for row in rows: writer.writerow([row.id, row.created_at.isoformat(), row.actor_id, row.action, row.object_type, row.object_id, row.result, row.trace_id])
    write_audit(db, "audit.export", "audit_log", None, actor, request, detail={"count": len(rows)}); db.commit(); return StreamingResponse(iter([output.getvalue().encode("utf-8-sig")]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-logs.csv"})


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if not artifact: raise not_found("产物")
    if artifact.artifact_type in SENSITIVE_ARTIFACT_TYPES and user.role not in {"admin", "tester"}:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "该产物包含敏感配置，仅管理员和测试人员可下载"})
    path = Path(artifact.path).resolve(); root = settings.artifact_root.resolve()
    if root not in path.parents or not path.is_file(): raise not_found("产物文件")
    write_audit(db, "artifact.download", "artifact", artifact.id, user, request); db.commit(); return FileResponse(path, media_type=artifact.content_type, filename=artifact.name)
