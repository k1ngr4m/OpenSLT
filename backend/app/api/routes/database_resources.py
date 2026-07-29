from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.adapters.database import DatabaseDiscoveryConfig, DatabaseOperationError, mysql_adapter, parse_select
from app.api.deps import admin_only, operators
from app.api.routes.common import database_http_error, database_resource, not_found
from app.core.database import get_db
from app.core.security import decrypt_secret
from app.models import Resource, User
from app.schemas import (
    DatabaseConfigItemOut,
    DatabaseDiscoveryOut,
    DatabaseDiscoveryRequest,
    DatabaseExportRequest,
    DatabaseSqlRequest,
)
from app.services.audit import write_audit
from app.services.database_config_catalog import list_database_config_items

router = APIRouter()

def _same_identity(value: typing.Union[str, None], other: typing.Union[str, None]) -> bool:
    return (value or "").strip().casefold() == (other or "").strip().casefold()


def database_discovery_config(
    payload: DatabaseDiscoveryRequest,
    stored: typing.Union[Resource, None],
) -> DatabaseDiscoveryConfig:
    database_password = payload.database_password or None
    if stored and not database_password:
        database_identity_matches = (
            stored.database_connection_mode == payload.database_connection_mode
            and _same_identity(stored.database_host, payload.database_host)
            and (stored.database_port or 3306) == payload.database_port
            and _same_identity(stored.database_username, payload.database_username)
        )
        if not database_identity_matches:
            raise DatabaseOperationError(
                "DATABASE_PASSWORD_REQUIRED",
                "数据库连接信息已修改，请重新输入数据库密码",
            )
        database_password = decrypt_secret(stored.encrypted_database_password)

    ssh_password = payload.password or None
    ssh_private_key = payload.private_key or None
    if payload.database_connection_mode == "ssh_tunnel" and stored:
        ssh_identity_matches = (
            stored.database_connection_mode == "ssh_tunnel"
            and _same_identity(stored.host, payload.host)
            and stored.ssh_port == payload.ssh_port
            and _same_identity(stored.username, payload.username)
            and stored.auth_type == payload.auth_type
        )
        if payload.auth_type == "password" and not ssh_password:
            if not ssh_identity_matches:
                raise DatabaseOperationError(
                    "SSH_PASSWORD_REQUIRED",
                    "SSH 跳板机连接信息已修改，请重新输入 SSH 密码",
                )
            ssh_password = decrypt_secret(stored.encrypted_password)
        if payload.auth_type == "private_key" and not ssh_private_key:
            if not ssh_identity_matches:
                raise DatabaseOperationError(
                    "SSH_PRIVATE_KEY_REQUIRED",
                    "SSH 跳板机连接信息已修改，请重新输入 SSH 私钥",
                )
            ssh_private_key = decrypt_secret(stored.encrypted_private_key)

    return DatabaseDiscoveryConfig(
        database_host=payload.database_host,
        database_port=payload.database_port,
        database_username=payload.database_username,
        database_password=database_password,
        database_tls_enabled=payload.database_tls_enabled,
        connection_mode=payload.database_connection_mode,
        ssh_host=payload.host,
        ssh_port=payload.ssh_port,
        ssh_username=payload.username,
        ssh_password=ssh_password,
        ssh_private_key=ssh_private_key,
    )


@router.post("/resources/database/discover", response_model=DatabaseDiscoveryOut)
async def discover_resource_databases(
    payload: DatabaseDiscoveryRequest,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> dict:
    stored = None
    if payload.resource_id is not None:
        stored = db.get(Resource, payload.resource_id)
        if not stored or stored.is_deleted:
            raise not_found("数据库资源")
        if stored.resource_type != "database":
            raise HTTPException(
                status_code=400,
                detail={"code": "DATABASE_RESOURCE_REQUIRED", "message": "所选资源不是数据库资源"},
            )
    try:
        config = database_discovery_config(payload, stored)
        databases, filtered_system_count = await mysql_adapter.discover_databases(config)
    except DatabaseOperationError as exc:
        write_audit(
            db,
            "database.discover",
            "resource",
            payload.resource_id,
            actor,
            request,
            result="failed",
            detail={
                "connection_mode": payload.database_connection_mode,
                "code": exc.code,
            },
        )
        db.commit()
        raise database_http_error(exc) from exc
    write_audit(
        db,
        "database.discover",
        "resource",
        payload.resource_id,
        actor,
        request,
        detail={
            "connection_mode": payload.database_connection_mode,
            "count": len(databases),
            "filtered_system_count": filtered_system_count,
        },
    )
    db.commit()
    return {
        "databases": databases,
        "filtered_system_count": filtered_system_count,
    }


@router.get(
    "/resources/{resource_id}/database/config-items",
    response_model=typing.List[DatabaseConfigItemOut],
)
async def database_config_items(
    resource_id: int,
    database_name: str,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> list[dict[str, typing.Optional[str]]]:
    resource, database_name = database_resource(db, resource_id, database_name)
    try:
        items = await list_database_config_items(resource, database_name)
    except DatabaseOperationError as exc:
        write_audit(
            db,
            "database.config_items",
            "resource",
            resource.id,
            actor,
            request,
            result="failed",
            detail={"database": database_name, "code": exc.code},
        )
        db.commit()
        raise database_http_error(exc) from exc
    write_audit(
        db,
        "database.config_items",
        "resource",
        resource.id,
        actor,
        request,
        detail={"database": database_name, "count": len(items)},
    )
    db.commit()
    return items


@router.post("/resources/{resource_id}/database/select")
async def database_select(
    resource_id: int,
    payload: DatabaseSqlRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_select(payload.sql, database_name)
        result = await mysql_adapter.select(resource, database_name, plan)
    except DatabaseOperationError as exc:
        write_audit(db, "database.select", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        write_audit(db, "database.select", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc
    write_audit(db, "database.select", "resource", resource.id, actor, request, detail={"database": database_name, "sql_fingerprint": plan.fingerprint, "row_count": result["row_count"]}); db.commit()
    return result

@router.post("/resources/{resource_id}/database/export")
async def database_export(
    resource_id: int,
    payload: DatabaseExportRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_select(payload.sql, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    filename = f"database-{resource.id}-{database_name}.{payload.format}"
    write_audit(db, "database.export", "resource", resource.id, actor, request, detail={"database": database_name, "format": payload.format, "sql_fingerprint": plan.fingerprint}); db.commit()
    if payload.format == "csv":
        return StreamingResponse(mysql_adapter.iter_csv(resource, database_name, plan), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    try:
        path = await mysql_adapter.write_xlsx(resource, database_name, plan)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename, background=BackgroundTask(path.unlink, missing_ok=True))
