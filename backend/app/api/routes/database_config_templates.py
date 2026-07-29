from __future__ import annotations

import typing

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import operators
from app.api.routes.common import not_found
from app.core.database import get_db
from app.models import DatabaseConfigTemplate, User
from app.schemas import (
    DatabaseConfigTemplateCreate,
    DatabaseConfigTemplateOut,
    DatabaseConfigTemplateRename,
)
from app.services.audit import write_audit


router = APIRouter()


def _template_for_actor(db: Session, template_id: int, actor: User) -> DatabaseConfigTemplate:
    template = db.scalar(
        select(DatabaseConfigTemplate).where(
            DatabaseConfigTemplate.id == template_id,
            DatabaseConfigTemplate.user_id == actor.id,
        )
    )
    if not template:
        raise not_found("数据库配置模板")
    return template


def _duplicate_name() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "DATABASE_CONFIG_TEMPLATE_NAME_EXISTS", "message": "模板名称已存在"},
    )


@router.get(
    "/database-config-templates",
    response_model=typing.List[DatabaseConfigTemplateOut],
)
def list_database_config_templates(
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> list[DatabaseConfigTemplate]:
    return list(
        db.scalars(
            select(DatabaseConfigTemplate)
            .where(DatabaseConfigTemplate.user_id == actor.id)
            .order_by(DatabaseConfigTemplate.updated_at.desc(), DatabaseConfigTemplate.id.desc())
        ).all()
    )


@router.post(
    "/database-config-templates",
    response_model=DatabaseConfigTemplateOut,
    status_code=201,
)
def create_database_config_template(
    payload: DatabaseConfigTemplateCreate,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> DatabaseConfigTemplate:
    normalized_name = payload.name.casefold()
    existing = db.scalar(
        select(DatabaseConfigTemplate.id).where(
            DatabaseConfigTemplate.user_id == actor.id,
            DatabaseConfigTemplate.normalized_name == normalized_name,
        )
    )
    if existing:
        raise _duplicate_name()
    template = DatabaseConfigTemplate(
        user_id=actor.id,
        name=payload.name,
        normalized_name=normalized_name,
        keys=payload.keys,
    )
    db.add(template)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_name() from exc
    write_audit(
        db,
        "database_config_template.create",
        "database_config_template",
        template.id,
        actor,
        request,
        detail={"name": template.name, "key_count": len(template.keys)},
    )
    db.commit()
    db.refresh(template)
    return template


@router.patch(
    "/database-config-templates/{template_id}",
    response_model=DatabaseConfigTemplateOut,
)
def rename_database_config_template(
    template_id: int,
    payload: DatabaseConfigTemplateRename,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> DatabaseConfigTemplate:
    template = _template_for_actor(db, template_id, actor)
    normalized_name = payload.new_name.casefold()
    duplicate = db.scalar(
        select(DatabaseConfigTemplate.id).where(
            DatabaseConfigTemplate.user_id == actor.id,
            DatabaseConfigTemplate.normalized_name == normalized_name,
            DatabaseConfigTemplate.id != template.id,
        )
    )
    if duplicate:
        raise _duplicate_name()
    previous_name = template.name
    template.name = payload.new_name
    template.normalized_name = normalized_name
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _duplicate_name() from exc
    write_audit(
        db,
        "database_config_template.rename",
        "database_config_template",
        template.id,
        actor,
        request,
        detail={"previous_name": previous_name, "name": template.name},
    )
    db.commit()
    db.refresh(template)
    return template


@router.delete("/database-config-templates/{template_id}", status_code=204)
def delete_database_config_template(
    template_id: int,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    template = _template_for_actor(db, template_id, actor)
    detail = {"name": template.name, "key_count": len(template.keys)}
    db.delete(template)
    write_audit(
        db,
        "database_config_template.delete",
        "database_config_template",
        template.id,
        actor,
        request,
        detail=detail,
    )
    db.commit()
    return Response(status_code=204)
