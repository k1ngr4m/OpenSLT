from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.adapters.database import DatabaseOperationError, mysql_adapter, parse_update
from app.api.deps import operators
from app.api.routes.common import database_http_error, database_resource
from app.core.database import get_db
from app.models import DatabaseUpdateConfirmation, User
from app.schemas import DatabaseSqlRequest, DatabaseUpdateExecuteRequest
from app.services.audit import write_audit

router = APIRouter()

@router.post("/resources/{resource_id}/database/update-preview")
async def database_update_preview(
    resource_id: int,
    payload: DatabaseSqlRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_update(payload.sql, database_name)
        estimated_rows = await mysql_adapter.preview_update(resource, database_name, plan)
        if estimated_rows > 1_000:
            raise DatabaseOperationError("UPDATE_LIMIT_EXCEEDED", "UPDATE 预计影响超过 1000 行", 409)
    except DatabaseOperationError as exc:
        write_audit(db, "database.update_preview", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        write_audit(db, "database.update_preview", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc

    confirmation = DatabaseUpdateConfirmation(
        id=str(uuid4()),
        resource_id=resource.id,
        actor_id=actor.id,
        database_name=database_name,
        table_name=plan.table_name or "",
        sql_fingerprint=plan.fingerprint,
        estimated_rows=estimated_rows,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(confirmation)
    write_audit(db, "database.update_preview", "resource", resource.id, actor, request, detail={"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "estimated_rows": estimated_rows})
    db.commit()
    return {
        "confirmation_id": confirmation.id,
        "database_name": database_name,
        "table_name": plan.table_name,
        "estimated_rows": estimated_rows,
        "expires_at": confirmation.expires_at,
    }


@router.post("/resources/{resource_id}/database/update-execute")
async def database_update_execute(
    resource_id: int,
    payload: DatabaseUpdateExecuteRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_update(payload.sql, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    confirmation = db.get(DatabaseUpdateConfirmation, payload.confirmation_id)
    now = datetime.now(timezone.utc)
    expires_at = confirmation.expires_at if confirmation else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        not confirmation
        or confirmation.resource_id != resource.id
        or confirmation.actor_id != actor.id
        or confirmation.database_name != database_name
        or confirmation.sql_fingerprint != plan.fingerprint
    ):
        raise HTTPException(status_code=409, detail={"code": "INVALID_CONFIRMATION", "message": "更新确认已失效或与 SQL 不匹配"})
    if confirmation.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_ALREADY_USED", "message": "更新确认已使用"})
    if not expires_at or expires_at <= now:
        confirmation.status = "expired"; db.commit()
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_EXPIRED", "message": "更新确认已过期，请重新预览"})
    if payload.confirmation_text != resource.name:
        raise HTTPException(status_code=400, detail={"code": "CONFIRMATION_TEXT_MISMATCH", "message": "请输入完整资源名称确认"})

    claimed = db.execute(
        update(DatabaseUpdateConfirmation)
        .where(DatabaseUpdateConfirmation.id == confirmation.id, DatabaseUpdateConfirmation.status == "pending")
        .values(status="executing")
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_ALREADY_USED", "message": "更新确认已使用"})
    db.commit()
    try:
        affected_rows = await mysql_adapter.execute_update(resource, database_name, plan, confirmation.estimated_rows)
    except DatabaseOperationError as exc:
        confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
        confirmation.status = "failed"; confirmation.completed_at = datetime.now(timezone.utc)
        write_audit(db, "database.update_execute", "resource", resource.id, actor, request, "failed", {"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
        confirmation.status = "failed"; confirmation.completed_at = datetime.now(timezone.utc)
        write_audit(db, "database.update_execute", "resource", resource.id, actor, request, "failed", {"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc
    confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
    confirmation.status = "executed"; confirmation.actual_rows = affected_rows; confirmation.completed_at = datetime.now(timezone.utc)
    write_audit(db, "database.update_execute", "resource", resource.id, actor, request, detail={"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "affected_rows": affected_rows}); db.commit()
    return {"affected_rows": affected_rows, "status": "executed"}
