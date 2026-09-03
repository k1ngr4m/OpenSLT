from __future__ import annotations

import asyncio
import typing

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_only, operators
from app.core.database import get_db
from app.core.logging import redact
from app.core.security import decrypt_secret, encrypt_secret
from app.models import SvnKnowledgeSource, User
from app.schemas import (
    KnowledgeSearchOut,
    KnowledgeSearchRequest,
    SvnConnectionTestOut,
    SvnKnowledgeConnectionTest,
    SvnKnowledgeSourceOut,
    SvnKnowledgeSourceWrite,
    SvnSyncStatusOut,
    SvnSyncTaskOut,
)
from app.services.audit import write_audit
from app.services.svn_knowledge import (
    active_svn_task,
    enqueue_svn_sync,
    normalize_include_paths,
    normalize_repository_url,
    published_index_matches,
    svn_client_status,
    search_vector_index,
    test_svn_connection,
)
from app.services.embedding import EmbeddingClient, normalize_embedding_base_url, test_embedding_connection


router = APIRouter(prefix="/smart-cases")


def _source(db: Session) -> typing.Optional[SvnKnowledgeSource]:
    return db.scalar(select(SvnKnowledgeSource).order_by(SvnKnowledgeSource.id).limit(1))


def _normalized(payload: SvnKnowledgeSourceWrite) -> typing.Tuple[str, typing.List[str], str]:
    try:
        if not payload.embedding_model.strip():
            raise ValueError("Embedding 模型名称不能为空")
        return (
            normalize_repository_url(payload.repository_url, payload.allow_insecure_http),
            normalize_include_paths(payload.include_paths),
            normalize_embedding_base_url(payload.embedding_base_url, payload.allow_insecure_embedding_http),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SVN_CONFIG", "message": str(exc)}) from exc


def _out(source: typing.Optional[SvnKnowledgeSource]) -> SvnKnowledgeSourceOut:
    if source is None:
        return SvnKnowledgeSourceOut(configured=False)
    return SvnKnowledgeSourceOut(
        configured=True,
        repository_url=source.repository_url,
        username=source.username,
        has_password=bool(source.encrypted_password),
        embedding_base_url=source.embedding_base_url,
        embedding_model=source.embedding_model,
        has_embedding_api_key=bool(source.encrypted_embedding_api_key),
        allow_insecure_embedding_http=source.allow_insecure_embedding_http,
        include_paths=list(source.include_paths),
        sync_interval_minutes=source.sync_interval_minutes,
        enabled=source.enabled,
        allow_insecure_http=source.allow_insecure_http,
        updated_at=source.updated_at,
    )


@router.get("/knowledge-source", response_model=SvnKnowledgeSourceOut)
def get_knowledge_source(
    _: User = Depends(operators),
    db: Session = Depends(get_db),
) -> SvnKnowledgeSourceOut:
    return _out(_source(db))


@router.put("/knowledge-source", response_model=SvnKnowledgeSourceOut)
def save_knowledge_source(
    payload: SvnKnowledgeSourceWrite,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> SvnKnowledgeSourceOut:
    repository_url, include_paths, embedding_base_url = _normalized(payload)
    if active_svn_task(db):
        raise HTTPException(status_code=409, detail={"code": "SVN_SYNC_RUNNING", "message": "同步任务运行期间不能修改知识源配置"})
    source = _source(db)
    previous_index_identity = None if source is None else (
        source.repository_url,
        tuple(source.include_paths),
        source.embedding_base_url,
        source.embedding_model,
    )
    identity_changed = source is not None and (source.repository_url != repository_url or source.username != payload.username.strip())
    if not payload.password and (source is None or identity_changed):
        raise HTTPException(status_code=422, detail={"code": "SVN_PASSWORD_REQUIRED", "message": "首次配置或修改仓库/账号时必须重新输入密码"})
    if source is None:
        source = SvnKnowledgeSource(
            repository_url=repository_url,
            username=payload.username.strip(),
            encrypted_password=encrypt_secret(payload.password) or "",
            embedding_base_url=embedding_base_url,
            embedding_model=payload.embedding_model.strip(),
            encrypted_embedding_api_key=encrypt_secret(payload.embedding_api_key),
            allow_insecure_embedding_http=payload.allow_insecure_embedding_http,
        )
        db.add(source)
    else:
        source.repository_url = repository_url
        source.username = payload.username.strip()
        if payload.password:
            source.encrypted_password = encrypt_secret(payload.password) or ""
        if source.embedding_base_url != embedding_base_url and not payload.embedding_api_key:
            source.encrypted_embedding_api_key = None
        elif payload.embedding_api_key:
            source.encrypted_embedding_api_key = encrypt_secret(payload.embedding_api_key)
        source.embedding_base_url = embedding_base_url
        source.embedding_model = payload.embedding_model.strip()
        source.allow_insecure_embedding_http = payload.allow_insecure_embedding_http
    source.include_paths = include_paths
    source.sync_interval_minutes = payload.sync_interval_minutes
    source.enabled = payload.enabled
    source.allow_insecure_http = payload.allow_insecure_http
    current_index_identity = (
        source.repository_url,
        tuple(source.include_paths),
        source.embedding_base_url,
        source.embedding_model,
    )
    if previous_index_identity is not None and previous_index_identity != current_index_identity:
        source.sync_status = "stale"
    db.flush()
    write_audit(
        db,
        "smart_cases.svn_config.save",
        "svn_knowledge_source",
        source.id,
        actor,
        request,
        detail={
            "repository_url": repository_url,
            "username": source.username,
            "include_paths": include_paths,
            "enabled": source.enabled,
            "allow_insecure_http": source.allow_insecure_http,
            "has_password": bool(source.encrypted_password),
            "embedding_base_url": embedding_base_url,
            "embedding_model": source.embedding_model,
            "has_embedding_api_key": bool(source.encrypted_embedding_api_key),
            "allow_insecure_embedding_http": source.allow_insecure_embedding_http,
        },
    )
    db.commit()
    db.refresh(source)
    return _out(source)


@router.post("/knowledge-source/connection-test", response_model=SvnConnectionTestOut)
async def connection_test(
    payload: SvnKnowledgeConnectionTest,
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> typing.Dict[str, typing.Any]:
    repository_url, include_paths, embedding_base_url = _normalized(payload)
    source = _source(db)
    identity_matches = source is not None and source.repository_url == repository_url and source.username == payload.username.strip()
    password = payload.password or (decrypt_secret(source.encrypted_password) if identity_matches else None)
    if not password:
        raise HTTPException(status_code=422, detail={"code": "SVN_PASSWORD_REQUIRED", "message": "连接测试需要 SVN 密码"})
    embedding_identity_matches = source is not None and source.embedding_base_url == embedding_base_url
    embedding_api_key = payload.embedding_api_key or (decrypt_secret(source.encrypted_embedding_api_key) if embedding_identity_matches else None)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            test_svn_connection,
            repository_url,
            payload.username.strip(),
            password,
            include_paths,
        )
        result["embedding_dimensions"] = await loop.run_in_executor(
            None,
            test_embedding_connection,
            embedding_base_url,
            payload.embedding_model.strip(),
            embedding_api_key,
        )
    except Exception as exc:
        write_audit(db, "smart_cases.svn_connection.test", "svn_knowledge_source", source.id if source else None, actor, request, result="failed")
        db.commit()
        raise HTTPException(status_code=502, detail={"code": "SVN_CONNECTION_FAILED", "message": str(redact(str(exc)))}) from exc
    write_audit(db, "smart_cases.svn_connection.test", "svn_knowledge_source", source.id if source else None, actor, request, detail={"repository_url": repository_url, "include_paths": include_paths})
    db.commit()
    return result


@router.post("/knowledge-source/sync", response_model=SvnSyncTaskOut, status_code=202)
def sync_now(
    request: Request,
    actor: User = Depends(admin_only),
    db: Session = Depends(get_db),
) -> SvnSyncTaskOut:
    source = _source(db)
    if source is None:
        raise HTTPException(status_code=409, detail={"code": "SVN_NOT_CONFIGURED", "message": "请先保存 SVN 知识源配置"})
    existing = active_svn_task(db)
    task = existing or enqueue_svn_sync(db, source, "manual")
    write_audit(db, "smart_cases.svn_sync.start", "svn_knowledge_source", source.id, actor, request, detail={"task_id": task.id, "reused": existing is not None})
    db.commit()
    return SvnSyncTaskOut(task_id=task.id, status=task.status, reused=existing is not None)


@router.get("/knowledge-source/sync-status", response_model=SvnSyncStatusOut)
def sync_status(
    _: User = Depends(operators),
    db: Session = Depends(get_db),
) -> SvnSyncStatusOut:
    source = _source(db)
    client = svn_client_status()
    task = active_svn_task(db)
    error = client["error"] if not client["ready"] else (source.last_error if source else None)
    return SvnSyncStatusOut(
        configured=source is not None,
        client_ready=client["ready"],
        svn_version=client["version"],
        embedding_model=source.embedding_model if source else None,
        embedding_dimensions=source.embedding_dimensions if source else None,
        status=task.status if task else (source.sync_status if source else "unconfigured"),
        task_id=task.id if task else None,
        last_attempt_at=source.last_attempt_at if source else None,
        last_success_at=source.last_success_at if source else None,
        revisions=dict(source.last_revisions) if source else {},
        file_count=source.file_count if source else 0,
        failed_file_count=source.failed_file_count if source else 0,
        changes=dict(source.last_changes) if source else {},
        error=error,
    )


@router.post("/knowledge-search", response_model=KnowledgeSearchOut)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    _: User = Depends(operators),
    db: Session = Depends(get_db),
) -> KnowledgeSearchOut:
    source = _source(db)
    if source is None:
        raise HTTPException(status_code=409, detail={"code": "SVN_NOT_CONFIGURED", "message": "请先配置并同步知识源"})
    if not published_index_matches(source):
        raise HTTPException(status_code=409, detail={"code": "KNOWLEDGE_INDEX_STALE", "message": "当前配置没有匹配的成功索引，请先完成同步"})
    api_key = decrypt_secret(source.encrypted_embedding_api_key)
    client = EmbeddingClient(source.embedding_base_url, source.embedding_model, api_key)
    loop = asyncio.get_running_loop()
    try:
        vectors = await loop.run_in_executor(None, client.embed, [payload.query.strip()])
        results = await loop.run_in_executor(None, search_vector_index, payload.query.strip(), vectors[0], payload.top_k)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "KNOWLEDGE_SEARCH_FAILED", "message": str(redact(str(exc)))}) from exc
    return KnowledgeSearchOut(query=payload.query.strip(), results=results)
