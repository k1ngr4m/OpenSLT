from __future__ import annotations

import typing
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app.api.router import router
from app.core.config import settings
from app.core.database import SessionLocal, engine, validate_database_server
from app.core.logging import configure_logging, logger, trace_id_ctx
from app.core.observability import writer
from app.core.observability_middleware import ObservabilityMiddleware
from app.core.security import CredentialSecretError, hash_password
from app.models import BusinessType, PlanDirectory, User
from app.services.durable_tasks import (
    claim_due_tasks,
    execute_claimed_task,
    recover_abandoned_tasks,
)
from app.services.orchestration import (
    archive_and_clean_logs,
    expire_timed_out_runs,
    reclaim_expired_locks,
)
from app.services.svn_knowledge import enqueue_due_svn_syncs, svn_client_status
from app.version import APP_VERSION


def seed_database() -> None:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == settings.initial_admin_username).first():
            db.add(User(username=settings.initial_admin_username, display_name="系统管理员", password_hash=hash_password(settings.initial_admin_password), role="admin"))
        for code, name in [("fut_mm", "软核做市"), ("rem_two", "整合版二期"), ("rem_two_mm", "整合版二期做市")]:
            if not db.query(BusinessType).filter(BusinessType.code == code).first(): db.add(BusinessType(code=code, name=name))
        if not db.query(PlanDirectory).filter(PlanDirectory.is_default.is_(True)).first():
            db.add(PlanDirectory(name="默认目录", is_default=True))
        db.commit()
    finally: db.close()


async def internal_scheduler() -> None:
    """Dispatch durable tasks and maintenance inside the API process."""
    loop = asyncio.get_running_loop()
    next_lock_reclaim = loop.time()
    next_retention_cleanup = loop.time() + 300
    next_observability_retry = loop.time() + 60
    next_svn_enqueue = loop.time()
    while True:
        now = loop.time()
        db = SessionLocal()
        try:
            if now >= next_lock_reclaim:
                expire_timed_out_runs(db)
                reclaim_expired_locks(db)
                next_lock_reclaim = now + 60
            if now >= next_svn_enqueue:
                enqueue_due_svn_syncs(db)
                next_svn_enqueue = now + 60
            task_ids = claim_due_tasks(db)
        except Exception:
            logger.exception("internal_scheduler_iteration_failed")
            task_ids = []
        finally:
            db.close()
        for task_id in task_ids:
            asyncio.create_task(execute_claimed_task(task_id))
        if now >= next_observability_retry:
            writer.retry_pending()
            next_observability_retry = now + 60
        if now >= next_retention_cleanup:
            db = SessionLocal()
            try:
                archive_and_clean_logs(db)
            except Exception:
                logger.exception("retention_cleanup_failed")
            finally:
                db.close()
            next_retention_cleanup = now + 86400
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    writer.start()
    with engine.connect() as connection:
        database_server = validate_database_server(connection)
    if database_server is not None:
        logger.info(
            "database_server_compatible",
            family=database_server.family,
            version=database_server.raw_version,
        )
    seed_database()
    svn_status = svn_client_status()
    if svn_status["ready"]:
        logger.info("svn_client_ready", version=svn_status["version"])
    else:
        logger.warning("svn_client_unavailable", message=svn_status["error"])
    recovery_db = SessionLocal()
    try:
        recovered = recover_abandoned_tasks(recovery_db)
        if recovered:
            logger.info("durable_tasks_recovered", count=recovered)
    finally:
        recovery_db.close()
    scheduler_task = None
    if settings.enable_internal_scheduler:
        scheduler_task = asyncio.create_task(internal_scheduler())
        logger.info("internal_scheduler_started")
    logger.info("application_started", version=APP_VERSION)
    try:
        yield
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        logger.info("application_stopped")
        writer.stop()


app = FastAPI(title=settings.app_name, version=APP_VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:7777", "http://127.0.0.1:7777"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(ObservabilityMiddleware)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    detail.setdefault("trace_id", trace_id_ctx.get()); return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = []
    for item in exc.errors():
        serialized = dict(item)
        serialized.pop("ctx", None)
        details.append(serialized)
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "请求参数校验失败", "details": details, "trace_id": trace_id_ctx.get()})


@app.exception_handler(CredentialSecretError)
async def credential_secret_error(request: Request, exc: CredentialSecretError):
    logger.error("credential_secret_error", path=request.url.path, message=exc.message)
    return JSONResponse(
        status_code=500,
        content={
            "code": "RESOURCE_CREDENTIAL_ERROR",
            "message": exc.message,
            "trace_id": trace_id_ctx.get(),
        },
    )


@app.get("/health")
def health() -> typing.Dict[str, str]:
    return {"status": "ok", "service": "openslt-api", "version": APP_VERSION}


app.include_router(router, prefix=settings.api_v1_prefix)


class SPAStaticFiles(StaticFiles):
    """Serve index.html for Vue Router history-mode routes."""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is not None and response.status_code != 404:
            return response
        if Path(path).suffix:
            if response is not None:
                return response
            raise StarletteHTTPException(status_code=404)
        return await super().get_response("index.html", scope)


if settings.frontend_dist and settings.frontend_dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
