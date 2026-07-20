from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.logging import configure_logging, logger, trace_id_ctx
from app.core.security import hash_password
from app.models import BusinessType, User


def seed_database() -> None:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == settings.initial_admin_username).first():
            db.add(User(username=settings.initial_admin_username, display_name="系统管理员", password_hash=hash_password(settings.initial_admin_password), role="admin"))
        for code, name in [("fut_mm", "软核做市"), ("rem_two", "整合版二期"), ("rem_two_mm", "整合版二期做市")]:
            if not db.query(BusinessType).filter(BusinessType.code == code).first(): db.add(BusinessType(code=code, name=name))
        db.commit()
    finally: db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(); Base.metadata.create_all(bind=engine); seed_database(); logger.info("application_started")
    yield
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

