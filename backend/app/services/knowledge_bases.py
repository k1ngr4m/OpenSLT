from __future__ import annotations

import json
import os
import shutil
import sqlite3
import typing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import redact
from app.core.security import decrypt_secret
from app.core.time import beijing_now
from app.models import AiModel, DurableTask, KnowledgeBase, KnowledgeUpload, ModelProvider, SvnKnowledgeSource
from app.services.embedding import EmbeddingClient
from app.services.model_providers import ModelProviderError
from app.services import svn_knowledge as svn


def resolve_base(db: Session, knowledge_base_id: typing.Optional[int] = None) -> typing.Optional[KnowledgeBase]:
    if knowledge_base_id is not None:
        base = db.get(KnowledgeBase, knowledge_base_id)
        if base is None:
            raise HTTPException(404, detail={"code": "KNOWLEDGE_BASE_NOT_FOUND", "message": "知识库不存在"})
        return base
    bases = list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id).limit(2)))
    if len(bases) > 1:
        raise HTTPException(409, detail={"code": "KNOWLEDGE_BASE_REQUIRED", "message": "请选择知识库"})
    return bases[0] if bases else None


def index_path(base_id: int) -> Path:
    return settings.knowledge_root / str(base_id) / "published" / "index.sqlite3"


def upload_path(item: KnowledgeUpload) -> Path:
    return settings.knowledge_root / str(item.knowledge_base_id) / "uploads" / item.storage_name


def upload_ref(item: KnowledgeUpload) -> str:
    return "upload/%s/%s" % (item.id, item.name)


def source_for(db: Session, base_id: int) -> typing.Optional[SvnKnowledgeSource]:
    return db.scalar(select(SvnKnowledgeSource).where(SvnKnowledgeSource.knowledge_base_id == base_id))


def embedding_model(db: Session, base: KnowledgeBase):
    model = db.get(AiModel, base.embedding_model_id) if base.embedding_model_id else None
    provider = db.get(ModelProvider, model.provider_id) if model else None
    if model is None or model.kind != "embedding" or provider is None or provider.user_id is not None:
        raise ModelProviderError("请为知识库配置有效的 Embedding 模型")
    return provider, model


def embedding_client(db: Session, base: KnowledgeBase) -> EmbeddingClient:
    provider, model = embedding_model(db, base)
    return EmbeddingClient(provider.base_url, model.model_id, decrypt_secret(provider.encrypted_api_key), expected_dimensions=provider.embedding_dimensions)


def index_ready(db: Session, base: KnowledgeBase) -> bool:
    if base.index_status == "stale":
        return False
    try:
        provider, model = embedding_model(db, base)
        path = index_path(base.id)
        if not path.is_file():
            return False
        with sqlite3.connect(str(path)) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            dimensions = connection.execute("SELECT DISTINCT dimensions FROM chunks").fetchall()
        manifest = json.loads(metadata.get("manifest", "{}"))
        source = source_for(db, base.id)
        repositories = (list(source.repository_urls or []) or [source.repository_url]) if source else []
        targets = {item[2] for item in svn._svn_targets(repositories, source.include_paths)} if source else set()
        return (metadata.get("embedding_model") == model.model_id and metadata.get("embedding_base_url") == provider.base_url
                and all(row[0] == provider.embedding_dimensions for row in dimensions)
                and manifest.get("chunk_size", 1200) == base.chunk_size and manifest.get("chunk_overlap", 150) == base.chunk_overlap
                and svn._manifest_repository_urls(manifest) == repositories
                and {key for key in manifest.get("revisions", {}) if not key.startswith("upload/")} == targets)
    except (sqlite3.Error, ValueError, ModelProviderError):
        return False


def active_index_task(db: Session, base_id: int) -> typing.Optional[DurableTask]:
    # JSONText is shared by SQLite/MySQL; filter the small active task set in Python.
    tasks = db.scalars(select(DurableTask).where(DurableTask.task_type.in_(["knowledge_index", "svn_sync"]),
                                               DurableTask.status.in_(["queued", "running"])).order_by(DurableTask.id.desc()))
    source = source_for(db, base_id)
    return next((task for task in tasks if task.payload.get("knowledge_base_id") == base_id or
                 (source is not None and task.task_type == "svn_sync" and task.payload.get("source_id") == source.id)), None)


def lock_idle(db: Session, base_id: int) -> KnowledgeBase:
    base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == base_id).with_for_update())
    if base is None:
        raise HTTPException(404, detail="知识库不存在")
    if active_index_task(db, base_id):
        raise HTTPException(409, detail={"code": "KNOWLEDGE_BUSY", "message": "知识库索引任务执行期间不能修改，请等待完成或取消任务"})
    return base


def enqueue_index(db: Session, base_id: int, sync_svn: bool = True, reason: str = "manual", now: typing.Optional[datetime] = None) -> DurableTask:
    from app.services.durable_tasks import enqueue_task
    base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == base_id).with_for_update())
    if base is None:
        raise ModelProviderError("知识库不存在")
    embedding_model(db, base)
    existing = active_index_task(db, base_id)
    if existing:
        return existing
    stamp = now or beijing_now()
    key = str(int(stamp.timestamp()) // 1800) if reason == "scheduled" else uuid4().hex
    task = enqueue_task(db, "knowledge_index", {"knowledge_base_id": base_id, "sync_svn": sync_svn or base.index_status == "stale"},
                        "knowledge-index:%s:%s:%s" % (base_id, reason, key))
    # Keep 'stale' until publication; an old model/chunk configuration must never be queried.
    if task.status in {"queued", "running"} and base.index_status != "stale":
        base.index_status = "queued"
    db.flush()
    return task


def documents(db: Session, base_id: int) -> list:
    manifest = svn._load_manifest(index_path(base_id))
    attempt = index_path(base_id).with_name("failed-attempt.json")
    if attempt.exists():
        failed_attempt = json.loads(attempt.read_text())
        for ref, message in failed_attempt.get("failed_files", {}).items():
            manifest.setdefault("files", {})[ref] = failed_attempt["files"][ref]
            manifest.setdefault("failed_files", {})[ref] = message
            manifest.setdefault("revisions", {}).update(failed_attempt["revisions"])
    counts = {}
    if index_path(base_id).exists():
        with sqlite3.connect(str(index_path(base_id))) as connection:
            counts = dict(connection.execute("SELECT source_path, COUNT(*) FROM chunks GROUP BY source_path"))
    failed = manifest.get("failed_files", {})
    result = {}
    for ref, item in manifest.get("files", {}).items():
        result[ref] = dict(source_path=ref, name=ref.rsplit("/", 1)[-1], origin="upload" if ref.startswith("upload/") else "svn",
                           revision=svn._revision_for_source(ref, manifest["revisions"]), size=item["size"], chunk_count=counts.get(ref, 0),
                           status="failed" if ref in failed else "indexed", error=failed.get(ref), upload_id=None)
    for item in db.scalars(select(KnowledgeUpload).where(KnowledgeUpload.knowledge_base_id == base_id).order_by(KnowledgeUpload.id)):
        ref = upload_ref(item)
        row = result.setdefault(ref, dict(source_path=ref, name=item.name, origin="upload", revision=item.sha256[:12],
                                         size=item.size, chunk_count=0, status="pending", error=None))
        row["upload_id"] = item.id
        if item.pending_delete:
            row["status"] = "deleting"
    return list(result.values())


def execute_index(base_id: int, sync_svn: bool = True, task_id: typing.Optional[int] = None, client=None) -> None:
    def check_cancelled():
        if task_id is not None:
            with SessionLocal() as session:
                task = session.get(DurableTask, task_id)
                if task is None or task.status == "cancelled" or task.payload.get("cancel_requested"):
                    raise svn.SvnSyncCancelled("索引任务已取消")

    manifest = {}
    try:
        with SessionLocal() as db:
            base = db.get(KnowledgeBase, base_id)
            if base is None:
                raise ValueError("知识库不存在")
            embedding = embedding_client(db, base)
            chunk_size, chunk_overlap = base.chunk_size, base.chunk_overlap
            source = source_for(db, base_id)
            repositories = (list(source.repository_urls or []) or [source.repository_url]) if source else []
            paths = list(source.include_paths) if source else []
            username, password = (source.username, decrypt_secret(source.encrypted_password) or "") if source else ("", "")
            if base.index_status != "stale":
                base.index_status = "running"
            if source and sync_svn:
                source.last_attempt_at = beijing_now()
                source.sync_status = "running"
            db.commit()
            uploads = list(db.scalars(select(KnowledgeUpload).where(KnowledgeUpload.knowledge_base_id == base_id)))
            # Values are consumed after closing the session; force loading before detaching.
            for item in uploads:
                _ = item.name, item.storage_name, item.size, item.sha256, item.pending_delete
        check_cancelled()
        previous = svn._load_manifest(index_path(base_id))
        working_copies, revisions = {}, {}
        if source and sync_svn:
            runner = client or svn.SvnClient()
            runner.check_cancelled = check_cancelled
            runner.version()
            for repository, relative, ref in svn._svn_targets(repositories, paths):
                check_cancelled()
                root, revision = svn._sync_working_copy(runner, repository, relative, username, password, base_id)
                working_copies[ref], revisions[ref] = root, revision
            manifest, changes = svn._build_manifest(repositories, revisions, working_copies, previous, check_cancelled)
        else:
            revisions = {key: value for key, value in previous.get("revisions", {}).items() if not key.startswith("upload/")}
            manifest = dict(repository_url=repositories[0] if repositories else "", repository_urls=repositories,
                            revisions=revisions, files={key: value for key, value in previous.get("files", {}).items() if not key.startswith("upload/")})
            changes = {}
            for repository, relative, ref in svn._svn_targets(repositories, paths):
                working_copies[ref] = svn._working_copy_path(repository, relative, base_id)
        for item in uploads:
            if item.pending_delete:
                continue
            path, ref = upload_path(item), upload_ref(item)
            manifest["files"][ref] = dict(size=item.size, mtime_ns=path.stat().st_mtime_ns, sha256=item.sha256)
            working_copies[ref] = path
            revisions[ref] = item.sha256[:12]
        manifest["published_at"] = beijing_now().isoformat()
        manifest["publication_id"] = uuid4().hex
        svn._publish_vector_index(manifest, previous, working_copies, embedding, check_cancelled,
                                  destination=index_path(base_id), chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                                  allow_empty=not manifest["files"])
        index_path(base_id).with_name("failed-attempt.json").unlink(missing_ok=True)
        with SessionLocal() as db:
            base = db.get(KnowledgeBase, base_id)
            base.index_status, base.last_error, base.last_success_at = "succeeded", None, beijing_now()
            base.legacy_index = False
            source = source_for(db, base_id)
            if source:
                source.sync_status, source.last_error = "succeeded", None
                source.last_success_at = base.last_success_at
                source.last_revisions = {key: value for key, value in revisions.items() if not key.startswith("upload/")}
                source.last_changes = changes
                source.embedding_dimensions = embedding.expected_dimensions
                source.file_count = len([key for key in manifest["files"] if not key.startswith("upload/") and key not in manifest.get("failed_files", {})])
                source.failed_file_count = len(manifest.get("failed_files", {}))
            for item in db.scalars(select(KnowledgeUpload).where(KnowledgeUpload.knowledge_base_id == base_id, KnowledgeUpload.pending_delete.is_(True))):
                upload_path(item).unlink(missing_ok=True)
                db.delete(item)
            db.commit()
    except Exception as exc:
        if manifest.get("failed_files"):
            attempt = index_path(base_id).with_name("failed-attempt.json")
            attempt.parent.mkdir(parents=True, exist_ok=True)
            temporary = attempt.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, attempt)
        with SessionLocal() as db:
            base = db.get(KnowledgeBase, base_id)
            cancelled = isinstance(exc, svn.SvnSyncCancelled)
            if base:
                if base.index_status != "stale":
                    base.index_status = "cancelled" if cancelled else "failed"
                base.last_error = None if cancelled else str(redact(str(exc)))[:1000]
            source = source_for(db, base_id)
            if source and sync_svn:
                source.sync_status = "cancelled" if cancelled else "failed"
                source.last_error = None if cancelled else str(redact(str(exc)))[:1000]
            task = db.get(DurableTask, task_id) if task_id else None
            if cancelled and task:
                task.status, task.finished_at = "cancelled", beijing_now()
                task.locked_by, task.lease_expires_at = None, None
            db.commit()
        if not isinstance(exc, svn.SvnSyncCancelled):
            raise


def migrate_legacy_indexes():
    with SessionLocal() as db:
        for base in db.scalars(select(KnowledgeBase).where(KnowledgeBase.legacy_index.is_(True))):
            source = source_for(db, base.id)
            try:
                provider, model = embedding_model(db, base)
                matches = source is not None and svn.published_index_matches(source, provider.base_url, model.model_id, provider.embedding_dimensions)
            except ModelProviderError:
                matches = False
            target = index_path(base.id)
            if matches:
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(".migration.tmp")
                    shutil.copy2(settings.knowledge_root / "published" / "svn-index.sqlite3", temporary)
                    os.replace(temporary, target)
                for repository, relative, _ in svn._svn_targets(list(source.repository_urls or []) or [source.repository_url], source.include_paths):
                    old = svn._working_copy_path(repository, relative)
                    new = svn._working_copy_path(repository, relative, base.id)
                    if old.exists() and not new.exists():
                        shutil.copytree(old, new)
                base.index_status, base.last_success_at = "succeeded", source.last_success_at
            else:
                base.index_status = "stale"
            base.legacy_index = False
        db.commit()


def index_revisions(base_id: int) -> dict:
    manifest = svn._load_manifest(index_path(base_id))
    revisions = dict(manifest.get("revisions", {}))
    if manifest.get("publication_id"):
        revisions["__publication__"] = manifest["publication_id"]
    return revisions
