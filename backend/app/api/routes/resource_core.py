from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.database import mysql_adapter
from app.adapters.ssh import ssh_adapter
from app.api.deps import admin_only, get_current_user, operators
from app.api.routes.common import not_found
from app.core.database import get_db
from app.core.security import decrypt_secret, encrypt_secret
from app.core.time import beijing_now
from app.models import BusinessType, PlanResource, Resource, ResourceLock, RunResource, ScenarioResource, User, WorkflowVersionResource
from app.schemas import ResourceOut, ResourceWrite
from app.services.audit import write_audit
from app.services.order_sessions import ssh_options

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

@router.post("/resources", response_model=ResourceOut, status_code=201)
def create_resource(payload: ResourceWrite, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> Resource:
    data = payload.model_dump(mode="json", exclude={"password", "private_key", "database_password"})
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
    data = payload.model_dump(mode="json", exclude={"password", "private_key", "database_password"})
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
        for link in (PlanResource, ScenarioResource, WorkflowVersionResource, RunResource)
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
        else:
            result = await ssh_adapter.check(host=resource.host, port=resource.ssh_port, username=resource.username, password=decrypt_secret(resource.encrypted_password), private_key=decrypt_secret(resource.encrypted_private_key))
            if result["ok"] and resource.resource_type == "order":
                import asyncssh
                import posixpath
                import shlex

                binary = str((resource.capabilities or {}).get("order_tool") or "").strip()
                command = (
                    "command -v tmux >/dev/null 2>&1 && test -d {workdir} && test -x {binary}"
                ).format(
                    workdir=shlex.quote(resource.remote_path),
                    binary=shlex.quote(posixpath.join(resource.remote_path, binary)),
                )
                async with asyncssh.connect(**ssh_options(resource)) as connection:
                    checked = await connection.run(command, check=False)
                if checked.exit_status != 0:
                    result = {"ok": False, "message": "发单资源缺少 tmux、工作目录或可执行程序"}
        resource.health_status = "healthy" if result["ok"] else "unhealthy"
    except Exception as exc:
        result = {"ok": False, "message": str(exc)}; resource.health_status = "unhealthy"
    resource.health_checked_at = beijing_now(); write_audit(db, "resource.health_check", "resource", resource.id, actor, request, result="success" if result["ok"] else "failed"); db.commit(); return result
