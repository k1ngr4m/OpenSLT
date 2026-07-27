from __future__ import annotations

import typing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.adapters.database import DatabaseDiscoveryConfig, DatabaseOperationError, mysql_adapter, parse_select, parse_update
from app.adapters.ssh import ssh_adapter
from app.api.deps import admin_only, get_current_user, operators
from app.api.routes.common import database_http_error, database_resource, not_found, order_config_http_error, order_config_resource, parser_config_resource
from app.core.database import get_db
from app.core.logging import trace_id_ctx
from app.core.security import decrypt_secret, encrypt_secret
from app.models import BusinessType, DatabaseUpdateConfirmation, Resource, ResourceLock, RunResource, ScenarioResource, User, WorkflowVersionResource
from app.schemas import DatabaseDiscoveryOut, DatabaseDiscoveryRequest, DatabaseExportRequest, DatabaseSqlRequest, DatabaseUpdateExecuteRequest, OrderConfigCreate, OrderConfigDetailOut, OrderConfigListOut, OrderConfigRename, OrderConfigUpdate, ResourceOut, ResourceWrite
from app.services.audit import write_audit
from app.services.order_configs import OrderConfigError, order_config_service

router = APIRouter()

@router.get("/business-types")
def list_business_types(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[dict]:
    return [{"id": row.id, "code": row.code, "name": row.name, "is_active": row.is_active} for row in db.scalars(select(BusinessType).order_by(BusinessType.id)).all()]


@router.get("/resources", response_model=typing.List[ResourceOut])
def list_resources(business_code: typing.Union[str, None] = None, resource_type: typing.Union[str, None] = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> typing.List[Resource]:
    query = select(Resource).where(Resource.is_deleted.is_(False))
    if business_code: query = query.where(Resource.business_code == business_code)
    if resource_type: query = query.where(Resource.resource_type == resource_type)
    return list(db.scalars(query.order_by(Resource.id.desc())).all())


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


@router.post("/resources", response_model=ResourceOut, status_code=201)
def create_resource(payload: ResourceWrite, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> Resource:
    data = payload.model_dump(exclude={"password", "private_key", "database_password"})
    resource = Resource(
        **data,
        encrypted_password=encrypt_secret(payload.password),
        encrypted_private_key=encrypt_secret(payload.private_key),
        encrypted_database_password=encrypt_secret(payload.database_password),
    )
    db.add(resource); db.flush(); write_audit(db, "resource.create", "resource", resource.id, actor, request, detail={"name": resource.name}); db.commit(); return resource


@router.put("/resources/{resource_id}", response_model=ResourceOut)
def update_resource(resource_id: int, payload: ResourceWrite, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> Resource:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted: raise not_found("资源")
    data = payload.model_dump(exclude={"password", "private_key", "database_password"})
    for key, value in data.items(): setattr(resource, key, value)
    if payload.password: resource.encrypted_password = encrypt_secret(payload.password)
    if payload.private_key: resource.encrypted_private_key = encrypt_secret(payload.private_key)
    if payload.database_password:
        resource.encrypted_database_password = encrypt_secret(payload.database_password)
    if payload.resource_type != "database":
        resource.encrypted_database_password = None
    write_audit(db, "resource.update", "resource", resource.id, actor, request); db.commit(); return resource


@router.delete("/resources/{resource_id}", status_code=204)
def delete_resource(resource_id: int, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> Response:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted: raise not_found("资源")
    referenced = any(
        db.scalar(select(link.id).where(link.resource_id == resource_id).limit(1)) is not None
        for link in (ScenarioResource, WorkflowVersionResource, RunResource)
    )
    active_lock = db.scalar(select(ResourceLock.id).where(ResourceLock.resource_id == resource_id, ResourceLock.released_at.is_(None)))
    if active_lock: raise HTTPException(status_code=409, detail={"code": "RESOURCE_IN_USE", "message": "资源正在被运行占用"})
    if referenced: resource.is_deleted = True; resource.is_enabled = False
    else: db.delete(resource)
    write_audit(db, "resource.delete", "resource", resource_id, actor, request, detail={"logical": bool(referenced)}); db.commit(); return Response(status_code=204)


@router.post("/resources/{resource_id}/health")
async def check_resource(resource_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> dict:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted: raise not_found("资源")
    try:
        if resource.resource_type == "database":
            result = await mysql_adapter.health(resource)
        else: result = await ssh_adapter.check(host=resource.host, port=resource.ssh_port, username=resource.username, password=decrypt_secret(resource.encrypted_password), private_key=decrypt_secret(resource.encrypted_private_key))
        resource.health_status = "healthy" if result["ok"] else "unhealthy"
    except Exception as exc:
        result = {"ok": False, "message": str(exc)}; resource.health_status = "unhealthy"
    resource.health_checked_at = datetime.now(timezone.utc); write_audit(db, "resource.health_check", "resource", resource.id, actor, request, result="success" if result["ok"] else "failed"); db.commit(); return result


def write_order_config_audit(
    db: Session,
    request: Request,
    actor: User,
    resource_id: int,
    action: str,
    result: str = "success",
    detail: typing.Union[dict, None] = None,
) -> None:
    write_audit(
        db,
        action,
        "resource",
        resource_id,
        actor,
        request,
        result=result,
        detail=detail,
    )
    db.commit()


@router.get("/resources/{resource_id}/order-configs", response_model=OrderConfigListOut)
async def list_order_configs(
    resource_id: int,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = order_config_resource(db, resource_id)
    try:
        result = await order_config_service.list(resource)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.list", "failed", {"code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.list",
        detail={"count": len(result["files"])},
    )
    return result


@router.get("/resources/{resource_id}/order-configs/{filename}", response_model=OrderConfigDetailOut)
async def read_order_config(
    resource_id: int,
    filename: str,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = order_config_resource(db, resource_id)
    try:
        result = await order_config_service.read(resource, filename)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.read", "failed", {"filename": filename, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.read",
        detail={"filename": filename, "checksum": result["checksum"]},
    )
    return result


@router.post("/resources/{resource_id}/order-configs", response_model=OrderConfigDetailOut, status_code=201)
async def create_order_config(
    resource_id: int,
    payload: OrderConfigCreate,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = order_config_resource(db, resource_id)
    try:
        result = await order_config_service.create(resource, payload.name, payload.source_name)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.create", "failed", {"filename": payload.name, "source_filename": payload.source_name, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.create",
        detail={"filename": result["name"], "source_filename": payload.source_name, "checksum": result["checksum"]},
    )
    return result


@router.put("/resources/{resource_id}/order-configs/{filename}", response_model=OrderConfigDetailOut)
async def update_order_config(
    resource_id: int,
    filename: str,
    payload: OrderConfigUpdate,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = order_config_resource(db, resource_id)
    try:
        result = await order_config_service.update(resource, filename, payload.content, payload.expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.update", "failed", {"filename": filename, "expected_checksum": payload.expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.update",
        detail={"filename": filename, "previous_checksum": payload.expected_checksum, "checksum": result["checksum"]},
    )
    return result


@router.patch("/resources/{resource_id}/order-configs/{filename}", response_model=OrderConfigDetailOut)
async def rename_order_config(
    resource_id: int,
    filename: str,
    payload: OrderConfigRename,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = order_config_resource(db, resource_id)
    try:
        result = await order_config_service.rename(resource, filename, payload.new_name, payload.expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.rename", "failed", {"filename": filename, "new_filename": payload.new_name, "expected_checksum": payload.expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.rename",
        detail={"filename": filename, "new_filename": result["name"], "checksum": result["checksum"]},
    )
    return result


@router.delete("/resources/{resource_id}/order-configs/{filename}", status_code=204)
async def delete_order_config(
    resource_id: int,
    filename: str,
    request: Request,
    expected_checksum: str = Query(..., pattern=r"^[0-9a-f]{64}$"),
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    resource = order_config_resource(db, resource_id)
    try:
        trash_name = await order_config_service.delete(resource, filename, expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "order_config.delete", "failed", {"filename": filename, "expected_checksum": expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "order_config.delete",
        detail={"filename": filename, "trash_name": trash_name, "checksum": expected_checksum},
    )
    return Response(status_code=204)


@router.get("/resources/{resource_id}/parser-configs", response_model=OrderConfigListOut)
async def list_parser_configs(
    resource_id: int,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await order_config_service.list(resource)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.list", "failed", {"code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.list", detail={"count": len(result["files"])})
    return result


@router.get("/resources/{resource_id}/parser-configs/{filename}", response_model=OrderConfigDetailOut)
async def read_parser_config(
    resource_id: int,
    filename: str,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await order_config_service.read(resource, filename)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.read", "failed", {"filename": filename, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.read", detail={"filename": filename, "checksum": result["checksum"]})
    return result


@router.post("/resources/{resource_id}/parser-configs", response_model=OrderConfigDetailOut, status_code=201)
async def create_parser_config(
    resource_id: int,
    payload: OrderConfigCreate,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await order_config_service.create(resource, payload.name, payload.source_name)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.create", "failed", {"filename": payload.name, "source_filename": payload.source_name, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.create", detail={"filename": result["name"], "source_filename": payload.source_name, "checksum": result["checksum"]})
    return result


@router.put("/resources/{resource_id}/parser-configs/{filename}", response_model=OrderConfigDetailOut)
async def update_parser_config(
    resource_id: int,
    filename: str,
    payload: OrderConfigUpdate,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await order_config_service.update(resource, filename, payload.content, payload.expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.update", "failed", {"filename": filename, "expected_checksum": payload.expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.update", detail={"filename": filename, "previous_checksum": payload.expected_checksum, "checksum": result["checksum"]})
    return result


@router.patch("/resources/{resource_id}/parser-configs/{filename}", response_model=OrderConfigDetailOut)
async def rename_parser_config(
    resource_id: int,
    filename: str,
    payload: OrderConfigRename,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await order_config_service.rename(resource, filename, payload.new_name, payload.expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.rename", "failed", {"filename": filename, "new_filename": payload.new_name, "expected_checksum": payload.expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.rename", detail={"filename": filename, "new_filename": result["name"], "checksum": result["checksum"]})
    return result


@router.delete("/resources/{resource_id}/parser-configs/{filename}", status_code=204)
async def delete_parser_config(
    resource_id: int,
    filename: str,
    request: Request,
    expected_checksum: str = Query(..., pattern=r"^[0-9a-f]{64}$"),
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    resource = parser_config_resource(db, resource_id)
    try:
        trash_name = await order_config_service.delete(resource, filename, expected_checksum)
    except OrderConfigError as exc:
        write_order_config_audit(db, request, actor, resource_id, "parser_config.delete", "failed", {"filename": filename, "expected_checksum": expected_checksum, "code": exc.code})
        raise order_config_http_error(exc) from exc
    write_order_config_audit(db, request, actor, resource_id, "parser_config.delete", detail={"filename": filename, "trash_name": trash_name, "checksum": expected_checksum})
    return Response(status_code=204)


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
