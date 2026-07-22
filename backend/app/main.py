import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from app.api.router import router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.logging import configure_logging, logger, trace_id_ctx
from app.core.security import hash_password
from app.models import BusinessType, User
from app.services.orchestration import (
    archive_and_clean_logs,
    expire_timed_out_runs,
    queued_run_ids,
    reclaim_expired_locks,
    start_run,
)


def seed_database() -> None:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == settings.initial_admin_username).first():
            db.add(User(username=settings.initial_admin_username, display_name="系统管理员", password_hash=hash_password(settings.initial_admin_password), role="admin"))
        for code, name in [("fut_mm", "软核做市"), ("rem_two", "整合版二期"), ("rem_two_mm", "整合版二期做市")]:
            if not db.query(BusinessType).filter(BusinessType.code == code).first(): db.add(BusinessType(code=code, name=name))
        db.commit()
    finally: db.close()


async def internal_scheduler() -> None:
    """Run the maintenance loop used by the Redis-free portable edition."""
    loop = asyncio.get_running_loop()
    next_lock_reclaim = loop.time()
    next_timeout_check = loop.time()
    next_retention_cleanup = loop.time() + 300
    while True:
        now = loop.time()
        db = SessionLocal()
        try:
            if now >= next_lock_reclaim:
                reclaim_expired_locks(db)
                next_lock_reclaim = now + 60
            if now >= next_timeout_check:
                expire_timed_out_runs(db)
                next_timeout_check = now + 30
            queued = queued_run_ids(db)
        except Exception:
            logger.exception("internal_scheduler_iteration_failed")
            queued = []
        finally:
            db.close()
        for run_id in queued:
            asyncio.create_task(start_run(run_id))
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
    Base.metadata.create_all(bind=engine)
    seed_database()
    scheduler_task = None
    if settings.portable_mode or settings.enable_internal_scheduler:
        scheduler_task = asyncio.create_task(internal_scheduler())
        logger.info("internal_scheduler_started")
    logger.info("application_started", portable_mode=settings.portable_mode)
    try:
        yield
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        logger.info("application_stopped")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id") or str(uuid4()); token = trace_id_ctx.set(trace_id)
    try:
        response = await call_next(request); response.headers["X-Trace-ID"] = trace_id; return response
    finally: trace_id_ctx.reset(token)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    detail.setdefault("trace_id", trace_id_ctx.get()); return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "请求参数校验失败", "details": exc.errors(), "trace_id": trace_id_ctx.get()})


@app.get("/health")
def health() -> dict[str, str]: return {"status": "ok", "service": "openslt-api"}


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


if settings.frontend_dist and settings.frontend_dist.is_dir() and settings.environment != "desktop":
    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
