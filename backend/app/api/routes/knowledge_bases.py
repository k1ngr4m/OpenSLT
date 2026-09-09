from __future__ import annotations

import hashlib
import typing
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_only, operators
from app.core.database import get_db
from app.core.time import beijing_now
from app.models import AiModel, DurableTask, KnowledgeBase, KnowledgeUpload, ModelProvider, User
from app.schemas import KnowledgeBaseCreate, KnowledgeBaseWrite, KnowledgeBaseOut, KnowledgeDocumentOut, SvnSyncTaskOut
from app.services import knowledge_bases as kb
from app.services.audit import write_audit
from app.services.model_providers import ModelProviderError
from app.services.svn_knowledge import SUPPORTED_SUFFIXES
from app.api.routes import smart_cases

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


def output(db, base):
    result = KnowledgeBaseOut.model_validate(base)
    if base.embedding_model_id:
        provider, model = kb.embedding_model(db, base)
        result.embedding_model, result.embedding_provider, result.embedding_dimensions = model.model_id, provider.name, provider.embedding_dimensions
    documents = kb.documents(db, base.id)
    result.document_count = len(documents)
    result.chunk_count = sum(item["chunk_count"] for item in documents)
    result.failed_count = sum(item["status"] == "failed" for item in documents)
    task = kb.active_index_task(db, base.id)
    if task:
        result.index_status = "cancelling" if task.payload.get("cancel_requested") else task.status
        result.task_id = task.id
    return result


def validate_model(db, model_id):
    model = db.get(AiModel, model_id)
    provider = db.get(ModelProvider, model.provider_id) if model else None
    if not model or model.kind != "embedding" or not provider or provider.user_id is not None:
        raise HTTPException(422, detail="请选择已保存的 Embedding 模型")


@router.get("", response_model=typing.List[KnowledgeBaseOut])
def list_bases(_: User = Depends(operators), db: Session = Depends(get_db)):
    return [output(db, base) for base in db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id))]


@router.post("", response_model=KnowledgeBaseOut, status_code=201)
def create_base(payload: KnowledgeBaseCreate, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    validate_model(db, payload.embedding_model_id)
    base = KnowledgeBase(**payload.model_dump())
    db.add(base)
    db.flush()
    write_audit(db, "knowledge_base.create", "knowledge_base", base.id, actor, request)
    db.commit()
    return output(db, base)


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def get_base(knowledge_base_id: int, _: User = Depends(operators), db: Session = Depends(get_db)):
    return output(db, kb.resolve_base(db, knowledge_base_id))


@router.put("/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def update_base(knowledge_base_id: int, payload: KnowledgeBaseWrite, request: Request,
                actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    base = kb.lock_idle(db, knowledge_base_id)
    if payload.embedding_model_id is not None and payload.embedding_model_id != base.embedding_model_id:
        if base.embedding_model_id is not None:
            raise HTTPException(409, detail="知识库的 Embedding 模型创建后无法修改")
        validate_model(db, payload.embedding_model_id)
        base.embedding_model_id = payload.embedding_model_id
        base.index_status = "stale"
    if (base.chunk_size, base.chunk_overlap) != (payload.chunk_size, payload.chunk_overlap):
        base.index_status = "stale"
    for key in ("name", "description", "chunk_size", "chunk_overlap", "top_k"):
        setattr(base, key, getattr(payload, key))
    write_audit(db, "knowledge_base.update", "knowledge_base", base.id, actor, request)
    db.commit()
    return output(db, base)


@router.get("/{knowledge_base_id}/documents", response_model=typing.List[KnowledgeDocumentOut])
def list_documents(knowledge_base_id: int, _: User = Depends(operators), db: Session = Depends(get_db)):
    kb.resolve_base(db, knowledge_base_id)
    return kb.documents(db, knowledge_base_id)


def enqueue(db, base_id, sync_svn=True):
    try:
        return kb.enqueue_index(db, base_id, sync_svn=sync_svn)
    except ModelProviderError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/{knowledge_base_id}/documents", response_model=SvnSyncTaskOut, status_code=202)
async def upload_documents(knowledge_base_id: int, request: Request, files: typing.List[UploadFile] = File(...),
                           actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    kb.lock_idle(db, knowledge_base_id)
    if not files or len(files) > 50:
        raise HTTPException(422, detail="每批上传 1–50 个文件")
    names = [file.filename or "" for file in files]
    # The KB row lock serializes uploads. Read current names even under MySQL REPEATABLE READ;
    # a full-name unique index exceeds legacy InnoDB's 767-byte limit.
    existing = set(db.scalars(select(KnowledgeUpload.name).where(
        KnowledgeUpload.knowledge_base_id == knowledge_base_id
    ).with_for_update()))
    if len(set(names)) != len(names) or existing.intersection(names):
        raise HTTPException(409, detail="存在同名上传文件，请更名后上传")
    for name in names:
        if not name.strip() or len(name) > 255 or name in {".", ".."} or any(char in name for char in '/\\') or any(ord(char) < 32 for char in name) or Path(name).suffix.casefold() not in SUPPORTED_SUFFIXES:
            raise HTTPException(422, detail="文件名或格式不支持：" + name[:100])
    written = []
    try:
        for file, name in zip(files, names):
            item = KnowledgeUpload(knowledge_base_id=knowledge_base_id, name=name, storage_name=uuid4().hex + Path(name).suffix.casefold(), size=0, sha256="")
            path = kb.upload_path(item)
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(path)
            digest = hashlib.sha256()
            with path.open("xb") as handle:
                path.chmod(0o600)
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    item.size += len(chunk)
                    if item.size > 50 * 1024 * 1024:
                        raise HTTPException(413, detail="单个文件不能超过 50 MiB")
                    handle.write(chunk)
                    digest.update(chunk)
            if not item.size:
                raise HTTPException(422, detail="不能上传空文件")
            item.sha256 = digest.hexdigest()
            db.add(item)
        db.flush()
        task = enqueue(db, knowledge_base_id, False)
        write_audit(db, "knowledge_base.upload", "knowledge_base", knowledge_base_id, actor, request, detail={"files": names})
        db.commit()
        return SvnSyncTaskOut(task_id=task.id, status=task.status, reused=False)
    except Exception:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise
    finally:
        for file in files:
            await file.close()


@router.delete("/{knowledge_base_id}/documents/{upload_id}", response_model=SvnSyncTaskOut, status_code=202)
def delete_document(knowledge_base_id: int, upload_id: int, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    kb.lock_idle(db, knowledge_base_id)
    item = db.get(KnowledgeUpload, upload_id)
    if item is None or item.knowledge_base_id != knowledge_base_id:
        raise HTTPException(404, detail="上传文档不存在")
    item.pending_delete = True
    task = enqueue(db, knowledge_base_id, False)
    write_audit(db, "knowledge_base.document.delete", "knowledge_base", knowledge_base_id, actor, request, detail={"upload_id": upload_id})
    db.commit()
    return SvnSyncTaskOut(task_id=task.id, status=task.status, reused=False)


@router.post("/{knowledge_base_id}/index", response_model=SvnSyncTaskOut, status_code=202)
def start_index(knowledge_base_id: int, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    kb.resolve_base(db, knowledge_base_id)
    existing = kb.active_index_task(db, knowledge_base_id)
    task = existing or enqueue(db, knowledge_base_id)
    write_audit(db, "knowledge_base.index", "knowledge_base", knowledge_base_id, actor, request)
    db.commit()
    return SvnSyncTaskOut(task_id=task.id, status=task.status, reused=existing is not None)


@router.post("/{knowledge_base_id}/index/cancel")
def cancel_index(knowledge_base_id: int, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)):
    base = kb.resolve_base(db, knowledge_base_id)
    task = kb.active_index_task(db, knowledge_base_id)
    if task is None:
        return {"status": base.index_status}
    task = db.scalar(select(DurableTask).where(DurableTask.id == task.id).with_for_update().execution_options(populate_existing=True))
    if task.status not in {"queued", "running"}:
        return {"status": task.status}
    task.payload = {**task.payload, "cancel_requested": True}
    if task.status == "queued":
        task.status, task.finished_at = "cancelled", beijing_now()
        if base.index_status != "stale":
            base.index_status = "cancelled"
    write_audit(db, "knowledge_base.index.cancel", "knowledge_base", knowledge_base_id, actor, request)
    db.commit()
    return {"status": "cancelled" if task.status == "cancelled" else "cancelling"}


router.add_api_route("/{knowledge_base_id}/svn", smart_cases.get_knowledge_source, methods=["GET"], response_model=smart_cases.SvnKnowledgeSourceOut)
router.add_api_route("/{knowledge_base_id}/svn", smart_cases.save_knowledge_source, methods=["PUT"], response_model=smart_cases.SvnKnowledgeSourceOut)
router.add_api_route("/{knowledge_base_id}/svn/connection-test", smart_cases.connection_test, methods=["POST"], response_model=smart_cases.SvnConnectionTestOut)
router.add_api_route("/{knowledge_base_id}/svn/status", smart_cases.sync_status, methods=["GET"], response_model=smart_cases.SvnSyncStatusOut)
router.add_api_route("/{knowledge_base_id}/search", smart_cases.search_knowledge, methods=["POST"], response_model=smart_cases.KnowledgeSearchOut)
