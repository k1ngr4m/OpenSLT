from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import operators
from app.api.routes.common import order_config_http_error, order_config_resource, parser_config_resource
from app.core.database import get_db
from app.models import User
from app.schemas import OrderConfigCreate, OrderConfigDetailOut, OrderConfigListOut, OrderConfigRename, OrderConfigUpdate, StatisticsScriptListOut
from app.services.audit import write_audit
from app.services.order_configs import OrderConfigError, order_config_service
from app.services.statistics_scripts import StatisticsScriptError, statistics_script_service

router = APIRouter()


def statistics_script_http_error(exc: StatisticsScriptError):
    from fastapi import HTTPException

    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})

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


@router.get("/resources/{resource_id}/statistics-scripts", response_model=StatisticsScriptListOut)
async def list_statistics_scripts(
    resource_id: int,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource = parser_config_resource(db, resource_id)
    try:
        result = await statistics_script_service.list(resource)
    except StatisticsScriptError as exc:
        write_order_config_audit(
            db, request, actor, resource_id, "statistics_script.list", "failed", {"code": exc.code}
        )
        raise statistics_script_http_error(exc) from exc
    write_order_config_audit(
        db,
        request,
        actor,
        resource_id,
        "statistics_script.list",
        detail={"count": len(result["files"])},
    )
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
