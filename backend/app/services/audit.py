from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import trace_id_ctx
from app.models import AuditLog, User


def write_audit(
    db: Session,
    action: str,
    object_type: str,
    object_id: int | str | None,
    actor: User | None = None,
    request: Request | None = None,
    result: str = "success",
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    record = AuditLog(
        actor_id=actor.id if actor else None,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        result=result,
        source_ip=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
        trace_id=trace_id_ctx.get(),
        detail=detail or {},
    )
    db.add(record)
    db.flush()
    return record

