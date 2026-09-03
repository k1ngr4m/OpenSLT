from __future__ import annotations

import asyncio
import typing

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import admin_only
from app.core.database import get_db
from app.core.logging import redact
from app.core.security import decrypt_secret, encrypt_secret
from app.models import ActiveAiModel, AiModel, ModelProvider, SmartCaseGeneration, SvnKnowledgeSource, User
from app.schemas import (
    AiModelCreate,
    AiModelOut,
    ModelConnectionTestOut,
    ModelDiscoveryOut,
    ModelDiscoveryRequest,
    ModelProviderOut,
    ModelProviderWrite,
)
from app.services.audit import write_audit
from app.services.embedding import test_embedding_connection
from app.services.llm import test_llm_connection
from app.services.model_providers import list_provider_models, normalize_provider_base_url
from app.services.svn_knowledge import active_svn_task


router = APIRouter(prefix="/model-providers")
ACTIVE_GENERATION_STATUSES = frozenset({"queued", "running"})


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _provider(db: Session, provider_id: int) -> ModelProvider:
    provider = db.scalar(
        select(ModelProvider)
        .where(ModelProvider.id == provider_id)
        .options(selectinload(ModelProvider.models))
    )
    if provider is None:
        raise _error(404, "MODEL_PROVIDER_NOT_FOUND", "模型提供商不存在")
    return provider


def _model(db: Session, model_id: int) -> AiModel:
    model = db.get(AiModel, model_id)
    if model is None:
        raise _error(404, "AI_MODEL_NOT_FOUND", "模型不存在")
    return model


def _active_ids(db: Session) -> typing.Set[int]:
    return set(db.scalars(select(ActiveAiModel.model_id)).all())


def _out(provider: ModelProvider, active_ids: typing.Set[int]) -> ModelProviderOut:
    return ModelProviderOut(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        has_api_key=bool(provider.encrypted_api_key),
        allow_insecure_http=provider.allow_insecure_http,
        models=[
            AiModelOut(
                id=model.id,
                provider_id=model.provider_id,
                kind=model.kind,
                model_id=model.model_id,
                is_active=model.id in active_ids,
            )
            for model in sorted(provider.models, key=lambda item: (item.kind, item.model_id.casefold()))
        ],
        updated_at=provider.updated_at,
    )


def _has_running_generation(db: Session, model_ids: typing.Iterable[int]) -> bool:
    ids = list(model_ids)
    return bool(ids) and db.scalar(
        select(SmartCaseGeneration.id)
        .where(
            SmartCaseGeneration.ai_model_id.in_(ids),
            SmartCaseGeneration.status.in_(ACTIVE_GENERATION_STATUSES),
        )
        .limit(1)
    ) is not None


def _mark_index_stale(db: Session) -> None:
    source = db.scalar(select(SvnKnowledgeSource).order_by(SvnKnowledgeSource.id).limit(1))
    if source is not None and source.last_success_at is not None:
        source.sync_status = "stale"


@router.get("", response_model=typing.List[ModelProviderOut])
def providers(
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> typing.List[ModelProviderOut]:
    items = list(
        db.scalars(select(ModelProvider).options(selectinload(ModelProvider.models)).order_by(ModelProvider.id)).all()
    )
    active_ids = _active_ids(db)
    return [_out(item, active_ids) for item in items]


@router.get("/models", response_model=typing.List[AiModelOut])
def models_by_kind(
    kind: typing.Literal["chat", "embedding"],
    _: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> typing.List[AiModelOut]:
    active_ids = _active_ids(db)
    return [
        AiModelOut(
            id=model.id,
            provider_id=model.provider_id,
            kind=model.kind,
            model_id=model.model_id,
            is_active=model.id in active_ids,
        )
        for model in db.scalars(
            select(AiModel).where(AiModel.kind == kind).order_by(AiModel.provider_id, AiModel.model_id)
        ).all()
    ]


@router.post("", response_model=ModelProviderOut, status_code=201)
def create_provider(
    payload: ModelProviderWrite,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> ModelProviderOut:
    name = payload.name.strip()
    if db.scalar(select(ModelProvider.id).where(ModelProvider.name == name)) is not None:
        raise _error(409, "MODEL_PROVIDER_EXISTS", "模型提供商名称已存在")
    try:
        base_url = normalize_provider_base_url(payload.base_url, payload.allow_insecure_http)
    except ValueError as exc:
        raise _error(422, "INVALID_MODEL_PROVIDER", str(exc)) from exc
    provider = ModelProvider(
        name=name,
        base_url=base_url,
        encrypted_api_key=encrypt_secret(payload.api_key),
        allow_insecure_http=payload.allow_insecure_http,
    )
    db.add(provider)
    db.flush()
    write_audit(db, "model_provider.create", "model_provider", provider.id, actor, request)
    db.commit()
    db.refresh(provider)
    return _out(provider, set())


@router.put("/{provider_id}", response_model=ModelProviderOut)
def update_provider(
    provider_id: int,
    payload: ModelProviderWrite,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> ModelProviderOut:
    provider = _provider(db, provider_id)
    name = payload.name.strip()
    duplicate = db.scalar(
        select(ModelProvider.id).where(ModelProvider.name == name, ModelProvider.id != provider.id)
    )
    if duplicate is not None:
        raise _error(409, "MODEL_PROVIDER_EXISTS", "模型提供商名称已存在")
    try:
        base_url = normalize_provider_base_url(payload.base_url, payload.allow_insecure_http)
    except ValueError as exc:
        raise _error(422, "INVALID_MODEL_PROVIDER", str(exc)) from exc
    active_ids = _active_ids(db)
    active_embedding = any(model.id in active_ids and model.kind == "embedding" for model in provider.models)
    connection_changed = (
        provider.base_url != base_url
        or provider.allow_insecure_http != payload.allow_insecure_http
        or bool(payload.api_key)
    )
    if connection_changed and active_embedding and active_svn_task(db):
        raise _error(409, "SVN_SYNC_RUNNING", "同步任务运行期间不能修改当前 Embedding 提供商")
    if connection_changed and _has_running_generation(db, (model.id for model in provider.models)):
        raise _error(409, "GENERATION_RUNNING", "用例生成任务运行期间不能修改其模型提供商")
    provider.name = name
    if provider.base_url != base_url and not payload.api_key:
        provider.encrypted_api_key = None
    elif payload.api_key:
        provider.encrypted_api_key = encrypt_secret(payload.api_key)
    provider.base_url = base_url
    provider.allow_insecure_http = payload.allow_insecure_http
    if connection_changed and active_embedding:
        _mark_index_stale(db)
    write_audit(db, "model_provider.update", "model_provider", provider.id, actor, request)
    db.commit()
    db.refresh(provider)
    return _out(provider, active_ids)


@router.delete("/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> None:
    provider = _provider(db, provider_id)
    model_ids = [model.id for model in provider.models]
    if _active_ids(db).intersection(model_ids):
        raise _error(409, "ACTIVE_MODEL_IN_USE", "提供商包含当前模型，请先切换当前模型")
    if _has_running_generation(db, model_ids):
        raise _error(409, "GENERATION_RUNNING", "提供商仍被运行中的用例生成任务使用")
    write_audit(db, "model_provider.delete", "model_provider", provider.id, actor, request)
    db.delete(provider)
    db.commit()


@router.post("/{provider_id}/models/discover", response_model=ModelDiscoveryOut)
async def discover_models(
    provider_id: int,
    _: ModelDiscoveryRequest,
    __: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> ModelDiscoveryOut:
    provider = _provider(db, provider_id)
    loop = asyncio.get_running_loop()
    try:
        model_ids = await loop.run_in_executor(
            None,
            list_provider_models,
            provider.base_url,
            decrypt_secret(provider.encrypted_api_key),
        )
    except Exception as exc:
        raise _error(502, "MODEL_DISCOVERY_FAILED", str(redact(str(exc)))) from exc
    return ModelDiscoveryOut(models=model_ids)


@router.post("/{provider_id}/models", response_model=AiModelOut, status_code=201)
def create_model(
    provider_id: int,
    payload: AiModelCreate,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> AiModelOut:
    provider = _provider(db, provider_id)
    model_name = payload.model_id.strip()
    if not model_name:
        raise _error(422, "INVALID_MODEL", "模型 ID 不能为空")
    existing = db.scalar(
        select(AiModel).where(
            AiModel.provider_id == provider.id,
            AiModel.kind == payload.kind,
            AiModel.model_id == model_name,
        )
    )
    if existing is not None:
        raise _error(409, "AI_MODEL_EXISTS", "该模型已经添加")
    model = AiModel(provider_id=provider.id, kind=payload.kind, model_id=model_name)
    db.add(model)
    db.flush()
    write_audit(db, "ai_model.create", "ai_model", model.id, actor, request)
    db.commit()
    db.refresh(model)
    return AiModelOut(id=model.id, provider_id=model.provider_id, kind=model.kind, model_id=model.model_id)


@router.post("/models/{model_id}/activate", response_model=AiModelOut)
def activate_model(
    model_id: int,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> AiModelOut:
    model = _model(db, model_id)
    selection = db.get(ActiveAiModel, model.kind)
    changed = selection is None or selection.model_id != model.id
    if changed and model.kind == "embedding" and active_svn_task(db):
        raise _error(409, "SVN_SYNC_RUNNING", "同步任务运行期间不能切换当前 Embedding 模型")
    if selection is None:
        db.add(ActiveAiModel(kind=model.kind, model_id=model.id))
    else:
        selection.model_id = model.id
    if changed and model.kind == "embedding":
        _mark_index_stale(db)
    write_audit(db, "ai_model.activate", "ai_model", model.id, actor, request)
    db.commit()
    return AiModelOut(
        id=model.id,
        provider_id=model.provider_id,
        kind=model.kind,
        model_id=model.model_id,
        is_active=True,
    )


@router.post("/models/{model_id}/connection-test", response_model=ModelConnectionTestOut)
async def test_model(
    model_id: int,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> ModelConnectionTestOut:
    model = _model(db, model_id)
    provider = db.get(ModelProvider, model.provider_id)
    api_key = decrypt_secret(provider.encrypted_api_key) if provider else None
    loop = asyncio.get_running_loop()
    try:
        if model.kind == "embedding":
            dimensions = await loop.run_in_executor(
                None, test_embedding_connection, provider.base_url, model.model_id, api_key
            )
        else:
            await loop.run_in_executor(
                None, test_llm_connection, provider.base_url, model.model_id, api_key
            )
            dimensions = None
    except Exception as exc:
        write_audit(db, "ai_model.connection_test", "ai_model", model.id, actor, request, result="failed")
        db.commit()
        raise _error(502, "MODEL_CONNECTION_FAILED", str(redact(str(exc)))) from exc
    write_audit(db, "ai_model.connection_test", "ai_model", model.id, actor, request)
    db.commit()
    return ModelConnectionTestOut(
        kind=model.kind,
        model_id=model.model_id,
        dimensions=dimensions,
    )


@router.delete("/models/{model_id}", status_code=204)
def delete_model(
    model_id: int,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> None:
    model = _model(db, model_id)
    if db.scalar(select(ActiveAiModel.kind).where(ActiveAiModel.model_id == model.id)) is not None:
        raise _error(409, "ACTIVE_MODEL_IN_USE", "当前模型不能删除，请先切换")
    if _has_running_generation(db, [model.id]):
        raise _error(409, "GENERATION_RUNNING", "模型仍被运行中的用例生成任务使用")
    write_audit(db, "ai_model.delete", "ai_model", model.id, actor, request)
    db.delete(model)
    db.commit()
