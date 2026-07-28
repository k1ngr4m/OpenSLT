from __future__ import annotations

import csv
import io
import typing
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_only, get_current_user
from app.api.routes.common import not_found
from app.core.config import settings
from app.core.database import get_db
from app.models import Artifact, AuditLog, LogRecord, User
from app.schemas import AuditOut, LogOut
from app.services.audit import write_audit

router = APIRouter()

@router.get("/logs", response_model=typing.List[LogOut])
def query_logs(log_type: typing.Union[str, None] = None, level: typing.Union[str, None] = None, trace_id: typing.Union[str, None] = None, keyword: typing.Union[str, None] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[LogRecord]:
    query = select(LogRecord)
    if user.role == "visitor": query = query.where(LogRecord.log_type.notin_(["command", "access"]))
    if log_type: query = query.where(LogRecord.log_type == log_type)
    if level: query = query.where(LogRecord.level == level.upper())
    if trace_id: query = query.where(LogRecord.trace_id == trace_id)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at.desc()).limit(1000)).all())


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
    path = Path(artifact.path).resolve(); root = settings.artifact_root.resolve()
    if root not in path.parents or not path.is_file(): raise not_found("产物文件")
    write_audit(db, "artifact.download", "artifact", artifact.id, user, request); db.commit(); return FileResponse(path, media_type=artifact.content_type, filename=artifact.name)
