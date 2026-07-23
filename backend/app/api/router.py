import csv
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from app.adapters.database import DatabaseOperationError, mysql_adapter, parse_select, parse_update, simulated_select, simulated_update_rows, validate_database
from app.adapters.ssh import ssh_adapter
from app.api.deps import admin_only, get_current_user, operators
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.logging import trace_id_ctx
from app.core.security import create_access_token, create_refresh_token, decode_token, decrypt_secret, encrypt_secret, hash_password, token_fingerprint, verify_password
from app.models import Artifact, AuditLog, BusinessType, DatabaseUpdateConfirmation, LogRecord, RefreshToken, Resource, ResourceLock, TestPlan, TestRun, TestScenario, User, Verdict
from app.schemas import ArtifactOut, AuditOut, DatabaseExportRequest, DatabaseSqlRequest, DatabaseUpdateExecuteRequest, LoginRequest, LogOut, PlanOut, PlanWrite, RefreshRequest, ResourceOut, ResourceWrite, RunCreate, RunOut, ScenarioOut, ScenarioWrite, TokenPair, UserCreate, UserOut, UserUpdate, VerdictOut, VerdictWrite
from app.services.audit import write_audit
from app.services.events import broker
from app.services.orchestration import TERMINAL_STATUSES, acquire_locks, cancel_run, continue_after_wiring, create_steps, release_locks, start_run
from app.services.reports import generate_reports

router = APIRouter()


def not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"{name}不存在"})


def validate_scenario_resources(db: Session, plan: TestPlan, resource_ids: list[int]) -> tuple[list[int], list[str]]:
    if not resource_ids:
        raise HTTPException(status_code=400, detail={"code": "SCENARIO_RESOURCES_REQUIRED", "message": "场景至少需要选择一个资源"})
    if len(resource_ids) != len(set(resource_ids)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCES", "message": "场景资源不能重复"})
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(resource_ids), Resource.is_deleted.is_(False), Resource.is_enabled.is_(True))).all())
    if len(resources) != len(resource_ids):
        raise HTTPException(status_code=400, detail={"code": "INVALID_RESOURCES", "message": "资源不存在或已停用"})
    resources_by_id = {resource.id: resource for resource in resources}
    ordered = [resources_by_id[resource_id] for resource_id in resource_ids]
    if any(resource.business_code != plan.business_code for resource in ordered):
        raise HTTPException(status_code=400, detail={"code": "BUSINESS_MISMATCH", "message": "资源与方案业务不一致"})
    resource_types = [resource.resource_type for resource in ordered]
    if len(resource_types) != len(set(resource_types)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCE_TYPES", "message": "每种资源类型最多选择一个资源"})
    return resource_ids, resource_types


def load_run(db: Session, run_id: int) -> TestRun:
    run = db.scalar(select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.steps), selectinload(TestRun.metrics), selectinload(TestRun.artifacts), selectinload(TestRun.verdict)))
    if not run:
        raise not_found("运行")
    return run


def database_http_error(exc: DatabaseOperationError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def database_resource(db: Session, resource_id: int, database_name: str) -> tuple[Resource, str]:
    resource = db.get(Resource, resource_id)
    if not resource or resource.is_deleted:
        raise not_found("资源")
    try:
        return resource, validate_database(resource, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc


@router.post("/auth/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        write_audit(db, "login", "user", user.id if user else payload.username, user, request, "failed")
        db.commit()
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误"})
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    decoded = decode_token(refresh, "refresh")
    db.add(RefreshToken(user_id=user.id, fingerprint=token_fingerprint(refresh), expires_at=datetime.fromtimestamp(decoded["exp"], UTC)))
    user.last_login_at = datetime.now(UTC)
    write_audit(db, "login", "user", user.id, user, request)
    db.commit()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=settings.jwt_access_minutes * 60)


@router.post("/auth/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    try:
        decoded = decode_token(payload.refresh_token, "refresh")
        stored = db.scalar(select(RefreshToken).where(RefreshToken.fingerprint == token_fingerprint(payload.refresh_token)))
        user = db.get(User, int(decoded["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError):
        stored = user = None
    now = datetime.now(UTC)
    if not stored or stored.revoked_at or stored.expires_at.replace(tzinfo=UTC) <= now or not user or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "INVALID_REFRESH_TOKEN", "message": "刷新令牌无效"})
    stored.revoked_at = now
    new_refresh = create_refresh_token(user.id)
    new_decoded = decode_token(new_refresh, "refresh")
    db.add(RefreshToken(user_id=user.id, fingerprint=token_fingerprint(new_refresh), expires_at=datetime.fromtimestamp(new_decoded["exp"], UTC)))
    db.commit()
    return TokenPair(access_token=create_access_token(user.id, user.role), refresh_token=new_refresh, expires_in=settings.jwt_access_minutes * 60)


@router.post("/auth/logout", status_code=204)
def logout(payload: RefreshRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    stored = db.scalar(select(RefreshToken).where(RefreshToken.fingerprint == token_fingerprint(payload.refresh_token)))
    if stored and stored.user_id == user.id:
        stored.revoked_at = datetime.now(UTC)
    write_audit(db, "logout", "user", user.id, user, request)
    db.commit()
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id)).all())


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> User:
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail={"code": "USERNAME_EXISTS", "message": "用户名已存在"})
    user = User(username=payload.username, display_name=payload.display_name, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user); db.flush(); write_audit(db, "user.create", "user", user.id, actor, request, detail={"username": user.username, "role": user.role}); db.commit(); return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if not user: raise not_found("用户")
    if user.id == actor.id and payload.is_active is False: raise HTTPException(status_code=400, detail={"code": "SELF_DISABLE", "message": "不能禁用当前账号"})
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    if password: user.password_hash = hash_password(password)
    for key, value in data.items(): setattr(user, key, value)
    write_audit(db, "user.update", "user", user.id, actor, request, detail={"fields": sorted(payload.model_fields_set)}); db.commit(); return user


@router.get("/business-types")
def list_business_types(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": row.id, "code": row.code, "name": row.name, "is_active": row.is_active} for row in db.scalars(select(BusinessType).order_by(BusinessType.id)).all()]


@router.get("/resources", response_model=list[ResourceOut])
def list_resources(business_code: str | None = None, resource_type: str | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Resource]:
    query = select(Resource).where(Resource.is_deleted.is_(False))
    if business_code: query = query.where(Resource.business_code == business_code)
    if resource_type: query = query.where(Resource.resource_type == resource_type)
    return list(db.scalars(query.order_by(Resource.id.desc())).all())


@router.post("/resources", response_model=ResourceOut, status_code=201)
def create_resource(payload: ResourceWrite, request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> Resource:
    data = payload.model_dump(exclude={"password", "private_key", "database_password"})
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
    data = payload.model_dump(exclude={"password", "private_key", "database_password"})
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
    referenced = any(resource_id in run.resource_ids for run in db.scalars(select(TestRun)).all())
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
        if resource.resource_type == "database" and settings.execution_mode == "simulated":
            result = {
                "ok": True,
                "message": "模拟模式连通",
                "details": [
                    {"database": name, "ok": True, "version": "MySQL simulated"}
                    for name in resource.database_names or []
                ],
                "version": "MySQL simulated",
                "simulated": True,
            }
        elif resource.resource_type == "database":
            result = await mysql_adapter.health(resource)
        elif settings.execution_mode == "simulated": result = {"ok": True, "message": "模拟模式连通", "simulated": True}
        else: result = await ssh_adapter.check(host=resource.host, port=resource.ssh_port, username=resource.username, password=decrypt_secret(resource.encrypted_password), private_key=decrypt_secret(resource.encrypted_private_key))
        resource.health_status = "healthy" if result["ok"] else "unhealthy"
    except Exception as exc:
        result = {"ok": False, "message": str(exc)}; resource.health_status = "unhealthy"
    resource.health_checked_at = datetime.now(UTC); write_audit(db, "resource.health_check", "resource", resource.id, actor, request, result="success" if result["ok"] else "failed"); db.commit(); return result


@router.post("/resources/{resource_id}/database/select")
async def database_select(
    resource_id: int,
    payload: DatabaseSqlRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_select(payload.sql, database_name)
        result = (
            simulated_select(plan)
            if settings.execution_mode == "simulated"
            else await mysql_adapter.select(resource, database_name, plan)
        )
    except DatabaseOperationError as exc:
        write_audit(db, "database.select", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        write_audit(db, "database.select", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc
    write_audit(db, "database.select", "resource", resource.id, actor, request, detail={"database": database_name, "sql_fingerprint": plan.fingerprint, "row_count": result["row_count"], "simulated": result["simulated"]}); db.commit()
    return result


@router.post("/resources/{resource_id}/database/update-preview")
async def database_update_preview(
    resource_id: int,
    payload: DatabaseSqlRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_update(payload.sql, database_name)
        simulated = settings.execution_mode == "simulated"
        estimated_rows = simulated_update_rows(plan) if simulated else await mysql_adapter.preview_update(resource, database_name, plan)
        if estimated_rows > 1_000:
            raise DatabaseOperationError("UPDATE_LIMIT_EXCEEDED", "UPDATE 预计影响超过 1000 行", 409)
    except DatabaseOperationError as exc:
        write_audit(db, "database.update_preview", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        write_audit(db, "database.update_preview", "resource", resource.id, actor, request, "failed", {"database": database_name, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc

    confirmation = DatabaseUpdateConfirmation(
        id=str(uuid4()),
        resource_id=resource.id,
        actor_id=actor.id,
        database_name=database_name,
        table_name=plan.table_name or "",
        sql_fingerprint=plan.fingerprint,
        estimated_rows=estimated_rows,
        simulated=simulated,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(confirmation)
    write_audit(db, "database.update_preview", "resource", resource.id, actor, request, detail={"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "estimated_rows": estimated_rows, "simulated": simulated})
    db.commit()
    return {
        "confirmation_id": confirmation.id,
        "database_name": database_name,
        "table_name": plan.table_name,
        "estimated_rows": estimated_rows,
        "expires_at": confirmation.expires_at,
        "simulated": simulated,
    }


@router.post("/resources/{resource_id}/database/update-execute")
async def database_update_execute(
    resource_id: int,
    payload: DatabaseUpdateExecuteRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> dict:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_update(payload.sql, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    confirmation = db.get(DatabaseUpdateConfirmation, payload.confirmation_id)
    now = datetime.now(UTC)
    expires_at = confirmation.expires_at if confirmation else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        not confirmation
        or confirmation.resource_id != resource.id
        or confirmation.actor_id != actor.id
        or confirmation.database_name != database_name
        or confirmation.sql_fingerprint != plan.fingerprint
    ):
        raise HTTPException(status_code=409, detail={"code": "INVALID_CONFIRMATION", "message": "更新确认已失效或与 SQL 不匹配"})
    if confirmation.status != "pending":
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_ALREADY_USED", "message": "更新确认已使用"})
    if not expires_at or expires_at <= now:
        confirmation.status = "expired"; db.commit()
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_EXPIRED", "message": "更新确认已过期，请重新预览"})
    if payload.confirmation_text != resource.name:
        raise HTTPException(status_code=400, detail={"code": "CONFIRMATION_TEXT_MISMATCH", "message": "请输入完整资源名称确认"})

    claimed = db.execute(
        update(DatabaseUpdateConfirmation)
        .where(DatabaseUpdateConfirmation.id == confirmation.id, DatabaseUpdateConfirmation.status == "pending")
        .values(status="executing")
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "CONFIRMATION_ALREADY_USED", "message": "更新确认已使用"})
    db.commit()
    try:
        affected_rows = (
            confirmation.estimated_rows
            if confirmation.simulated
            else await mysql_adapter.execute_update(resource, database_name, plan, confirmation.estimated_rows)
        )
    except DatabaseOperationError as exc:
        confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
        confirmation.status = "failed"; confirmation.completed_at = datetime.now(UTC)
        write_audit(db, "database.update_execute", "resource", resource.id, actor, request, "failed", {"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "code": exc.code}); db.commit()
        raise database_http_error(exc) from exc
    except Exception as exc:
        confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
        confirmation.status = "failed"; confirmation.completed_at = datetime.now(UTC)
        write_audit(db, "database.update_execute", "resource", resource.id, actor, request, "failed", {"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "code": "DATABASE_OPERATION_FAILED"}); db.commit()
        raise HTTPException(status_code=502, detail={"code": "DATABASE_OPERATION_FAILED", "message": str(exc)}) from exc
    confirmation = db.get(DatabaseUpdateConfirmation, confirmation.id)
    confirmation.status = "executed"; confirmation.actual_rows = affected_rows; confirmation.completed_at = datetime.now(UTC)
    write_audit(db, "database.update_execute", "resource", resource.id, actor, request, detail={"database": database_name, "table": plan.table_name, "sql_fingerprint": plan.fingerprint, "affected_rows": affected_rows, "simulated": confirmation.simulated}); db.commit()
    return {"affected_rows": affected_rows, "simulated": confirmation.simulated, "status": "executed"}


@router.post("/resources/{resource_id}/database/export")
async def database_export(
    resource_id: int,
    payload: DatabaseExportRequest,
    request: Request,
    actor: User = Depends(operators),
    db: Session = Depends(get_db),
) -> Response:
    resource, database_name = database_resource(db, resource_id, payload.database_name)
    try:
        plan = parse_select(payload.sql, database_name)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    simulated = settings.execution_mode == "simulated"
    filename = f"database-{resource.id}-{database_name}.{payload.format}"
    write_audit(db, "database.export", "resource", resource.id, actor, request, detail={"database": database_name, "format": payload.format, "sql_fingerprint": plan.fingerprint, "simulated": simulated}); db.commit()
    if simulated:
        result = simulated_select(plan)
        if payload.format == "csv":
            output = io.StringIO(); writer = csv.writer(output); writer.writerow(result["columns"])
            for row in result["rows"]: writer.writerow([row[column] for column in result["columns"]])
            return StreamingResponse(iter([output.getvalue().encode("utf-8-sig")]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-OpenSLT-Simulated": "true"})
        output = io.BytesIO(); workbook = Workbook(); worksheet = workbook.active; worksheet.title = "Query"; worksheet.append(result["columns"])
        for row in result["rows"]: worksheet.append([row[column] for column in result["columns"]])
        workbook.save(output); output.seek(0)
        return StreamingResponse(iter([output.getvalue()]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-OpenSLT-Simulated": "true"})
    if payload.format == "csv":
        return StreamingResponse(mysql_adapter.iter_csv(resource, database_name, plan), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    try:
        path = await mysql_adapter.write_xlsx(resource, database_name, plan)
    except DatabaseOperationError as exc:
        raise database_http_error(exc) from exc
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename, background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/plans", response_model=list[PlanOut])
def list_plans(business_code: str | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TestPlan]:
    query = select(TestPlan)
    if business_code: query = query.where(TestPlan.business_code == business_code)
    return list(db.scalars(query.order_by(TestPlan.id.desc())).all())


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(payload: PlanWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    plan = TestPlan(**payload.model_dump(), created_by=actor.id); db.add(plan); db.flush(); write_audit(db, "plan.create", "test_plan", plan.id, actor, request); db.commit(); return plan


@router.put("/plans/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: int, payload: PlanWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    plan = db.get(TestPlan, plan_id)
    if not plan: raise not_found("方案")
    for key, value in payload.model_dump().items(): setattr(plan, key, value)
    write_audit(db, "plan.update", "test_plan", plan.id, actor, request); db.commit(); return plan


@router.post("/plans/{plan_id}/copy", response_model=PlanOut, status_code=201)
def copy_plan(plan_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestPlan:
    original = db.scalar(select(TestPlan).where(TestPlan.id == plan_id).options(selectinload(TestPlan.scenarios)))
    if not original: raise not_found("方案")
    copied = TestPlan(name=f"{original.name} - 副本", business_code=original.business_code, description=original.description, default_resource_ids=list(original.default_resource_ids), config_version=original.config_version, created_by=actor.id)
    db.add(copied); db.flush()
    for scenario in original.scenarios: db.add(TestScenario(plan_id=copied.id, name=scenario.name, scenario_type=scenario.scenario_type, config_version=scenario.config_version, expected_artifacts=scenario.expected_artifacts, default_resource_ids=list(scenario.default_resource_ids), required_resource_types=list(scenario.required_resource_types), is_enabled=scenario.is_enabled))
    write_audit(db, "plan.copy", "test_plan", copied.id, actor, request, detail={"source_id": plan_id}); db.commit(); return copied


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Response:
    plan = db.get(TestPlan, plan_id)
    if not plan: raise not_found("方案")
    if db.scalar(select(TestRun.id).where(TestRun.plan_id == plan_id).limit(1)): raise HTTPException(status_code=409, detail={"code": "PLAN_REFERENCED", "message": "方案已有运行历史，只能停用"})
    db.delete(plan); write_audit(db, "plan.delete", "test_plan", plan_id, actor, request); db.commit(); return Response(status_code=204)


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(plan_id: int | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TestScenario]:
    query = select(TestScenario)
    if plan_id: query = query.where(TestScenario.plan_id == plan_id)
    return list(db.scalars(query.order_by(TestScenario.id.desc())).all())


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(payload: ScenarioWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    plan = db.get(TestPlan, payload.plan_id)
    if not plan: raise not_found("方案")
    values = payload.model_dump(exclude={"default_resource_ids"})
    if payload.default_resource_ids is not None:
        resource_ids, resource_types = validate_scenario_resources(db, plan, payload.default_resource_ids)
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    else:
        values["default_resource_ids"] = []
    scenario = TestScenario(**values); db.add(scenario); db.flush(); write_audit(db, "scenario.create", "test_scenario", scenario.id, actor, request); db.commit(); return scenario


@router.put("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: int, payload: ScenarioWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario: raise not_found("场景")
    plan = db.get(TestPlan, payload.plan_id)
    if not plan: raise not_found("方案")
    values = payload.model_dump(exclude={"default_resource_ids"})
    if payload.default_resource_ids is not None:
        resource_ids, resource_types = validate_scenario_resources(db, plan, payload.default_resource_ids)
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    elif scenario.default_resource_ids:
        resource_ids, resource_types = validate_scenario_resources(db, plan, scenario.default_resource_ids)
        values["default_resource_ids"] = resource_ids
        values["required_resource_types"] = resource_types
    for key, value in values.items(): setattr(scenario, key, value)
    write_audit(db, "scenario.update", "test_scenario", scenario.id, actor, request); db.commit(); return scenario


@router.post("/scenarios/{scenario_id}/copy", response_model=ScenarioOut, status_code=201)
def copy_scenario(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestScenario:
    source = db.get(TestScenario, scenario_id)
    if not source: raise not_found("场景")
    copied = TestScenario(plan_id=source.plan_id, name=f"{source.name} - 副本", scenario_type=source.scenario_type, config_version=source.config_version, expected_artifacts=source.expected_artifacts, default_resource_ids=list(source.default_resource_ids), required_resource_types=list(source.required_resource_types))
    db.add(copied); db.flush(); write_audit(db, "scenario.copy", "test_scenario", copied.id, actor, request, detail={"source_id": scenario_id}); db.commit(); return copied


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Response:
    scenario = db.get(TestScenario, scenario_id)
    if not scenario: raise not_found("场景")
    if db.scalar(select(TestRun.id).where(TestRun.scenario_id == scenario_id).limit(1)):
        raise HTTPException(status_code=409, detail={"code": "SCENARIO_REFERENCED", "message": "场景已有运行历史，只能停用"})
    db.delete(scenario); write_audit(db, "scenario.delete", "test_scenario", scenario_id, actor, request); db.commit(); return Response(status_code=204)


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(payload: RunCreate, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    plan = db.get(TestPlan, payload.plan_id); scenario = db.get(TestScenario, payload.scenario_id)
    if not plan or not plan.is_enabled: raise not_found("可用方案")
    if not scenario or not scenario.is_enabled or scenario.plan_id != plan.id: raise not_found("可用场景")
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(payload.resource_ids), Resource.is_deleted.is_(False), Resource.is_enabled.is_(True))).all())
    if len(resources) != len(set(payload.resource_ids)): raise HTTPException(status_code=400, detail={"code": "INVALID_RESOURCES", "message": "资源不存在或已停用"})
    resources_by_id = {resource.id: resource for resource in resources}
    resources = [resources_by_id[resource_id] for resource_id in payload.resource_ids]
    if any(resource.business_code != plan.business_code for resource in resources): raise HTTPException(status_code=400, detail={"code": "BUSINESS_MISMATCH", "message": "资源与方案业务不一致"})
    provided_types = [resource.resource_type for resource in resources]
    if len(provided_types) != len(set(provided_types)):
        raise HTTPException(status_code=400, detail={"code": "DUPLICATE_RESOURCE_TYPES", "message": "每种资源类型只能选择一个资源"})
    required_types = set(scenario.required_resource_types)
    if set(provided_types) != required_types:
        missing = sorted(required_types - set(provided_types))
        extra = sorted(set(provided_types) - required_types)
        raise HTTPException(status_code=400, detail={"code": "RESOURCE_SET_MISMATCH", "message": f"运行资源类型与场景不一致，缺少: {missing}，多余: {extra}"})
    snapshot = {"plan": {"id": plan.id, "name": plan.name, "business_code": plan.business_code, "config_version": plan.config_version}, "scenario": {"id": scenario.id, "name": scenario.name, "scenario_type": scenario.scenario_type, "config_version": scenario.config_version}, "resources": [{"id": resource.id, "name": resource.name, "type": resource.resource_type, "host": resource.host, "version": resource.version_info} for resource in resources]}
    run = TestRun(run_number=f"R{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:6].upper()}", plan_id=plan.id, scenario_id=scenario.id, business_code=plan.business_code, resource_ids=payload.resource_ids, config_snapshot=snapshot, trace_id=trace_id_ctx.get() or str(uuid4()), created_by=actor.id, timeout_at=datetime.now(UTC) + timedelta(minutes=payload.timeout_minutes))
    create_steps(run); db.add(run); db.flush(); write_audit(db, "run.create", "test_run", run.id, actor, request); db.commit(); return load_run(db, run.id)


@router.get("/runs", response_model=list[RunOut])
def list_runs(business_code: str | None = None, run_status: str | None = Query(default=None, alias="status"), conclusion: str | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TestRun]:
    query = select(TestRun).options(selectinload(TestRun.steps), selectinload(TestRun.metrics), selectinload(TestRun.artifacts), selectinload(TestRun.verdict))
    if business_code: query = query.where(TestRun.business_code == business_code)
    if run_status: query = query.where(TestRun.status == run_status)
    if conclusion: query = query.join(Verdict).where(Verdict.final_result == conclusion)
    return list(db.scalars(query.order_by(TestRun.id.desc()).limit(200)).unique().all())


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> TestRun:
    return load_run(db, run_id)


@router.post("/runs/{run_id}/start", response_model=RunOut)
def run_start(run_id: int, background: BackgroundTasks, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status not in {"draft", "resource_queue"}: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能启动"})
    run.status = "resource_queue"; write_audit(db, "run.start", "test_run", run.id, actor, request); db.commit(); background.add_task(start_run, run.id); return run


@router.post("/runs/{run_id}/confirm-wiring", response_model=RunOut)
def confirm_wiring(run_id: int, background: BackgroundTasks, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status != "awaiting_wiring": raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前不等待接线确认"})
    write_audit(db, "run.wiring_confirm", "test_run", run.id, actor, request); db.commit(); background.add_task(continue_after_wiring, run.id); return run


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def run_cancel(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status in TERMINAL_STATUSES: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "运行已结束"})
    cancel_run(db, run); write_audit(db, "run.cancel", "test_run", run.id, actor, request); db.commit(); return load_run(db, run.id)


@router.post("/runs/{run_id}/pause", response_model=RunOut)
def run_pause(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status not in {"resource_queue", "awaiting_wiring", "awaiting_review"}:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "仅排队或人工节点可安全暂停"})
    run.paused_from = run.status; run.status = "paused"
    write_audit(db, "run.pause", "test_run", run.id, actor, request); db.commit(); return run


@router.post("/runs/{run_id}/resume", response_model=RunOut)
def run_resume(run_id: int, background: BackgroundTasks, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status != "paused" or not run.paused_from:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "运行未暂停"})
    previous = run.paused_from; run.status = previous; run.paused_from = None
    write_audit(db, "run.resume", "test_run", run.id, actor, request, detail={"resume_to": previous}); db.commit()
    if previous == "resource_queue": background.add_task(start_run, run.id)
    return run


@router.post("/runs/{run_id}/retry", response_model=RunOut)
def run_retry(run_id: int, background: BackgroundTasks, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> TestRun:
    run = load_run(db, run_id)
    if run.status not in {"precheck_failed", "execution_failed", "parse_failed"}:
        raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能重试"})
    failed = next((step for step in run.steps if step.status == "failed"), None)
    if failed:
        failed.status = "pending"; failed.error_message = None; failed.retry_count += 1
    run.status = "resource_queue"; run.error_code = None; run.error_message = None; run.finished_at = None
    write_audit(db, "run.retry", "test_run", run.id, actor, request, detail={"step": failed.code if failed else None}); db.commit(); background.add_task(start_run, run.id); return run


@router.post("/runs/{run_id}/verdict", response_model=VerdictOut)
def submit_verdict(run_id: int, payload: VerdictWrite, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> Verdict:
    run = load_run(db, run_id)
    if run.status not in {"awaiting_review", "completed"}: raise HTTPException(status_code=409, detail={"code": "INVALID_TRANSITION", "message": "当前状态不能提交结论"})
    verdict = run.verdict or Verdict(run_id=run.id)
    verdict.final_result = payload.final_result; verdict.issue_description = payload.issue_description; verdict.notes = payload.notes; verdict.reviewed_by = actor.id; verdict.reviewed_at = datetime.now(UTC)
    if not run.verdict: db.add(verdict)
    review = next(step for step in run.steps if step.code == "manual_review"); review.status = "succeeded"; review.progress = 100; review.started_at = review.started_at or verdict.reviewed_at; review.finished_at = verdict.reviewed_at; review.duration_ms = 0
    report_step = next(step for step in run.steps if step.code == "reporting"); report_step.status = "running"; report_step.started_at = datetime.now(UTC)
    db.flush(); generate_reports(db, run)
    report_step.status = "succeeded"; report_step.progress = 100; report_step.finished_at = datetime.now(UTC); report_step.duration_ms = int((report_step.finished_at - report_step.started_at).total_seconds() * 1000)
    run.status = "completed"; run.progress = 100; run.finished_at = datetime.now(UTC); release_locks(db, run.id, "completed")
    write_audit(db, "run.verdict_submit", "test_run", run.id, actor, request, detail={"final_result": payload.final_result}); db.commit(); broker.publish(run.id, {"type": "status", "status": "completed", "progress": 100}); return verdict


@router.post("/runs/{run_id}/reports", response_model=list[ArtifactOut])
def regenerate_reports(run_id: int, request: Request, actor: User = Depends(operators), db: Session = Depends(get_db)) -> list[Artifact]:
    run = load_run(db, run_id)
    if run.status != "completed": raise HTTPException(status_code=409, detail={"code": "RUN_NOT_COMPLETE", "message": "运行完成后才能生成报告"})
    artifacts = generate_reports(db, run); write_audit(db, "report.regenerate", "test_run", run.id, actor, request); db.commit(); return artifacts


@router.get("/runs/{run_id}/logs", response_model=list[LogOut])
def list_run_logs(run_id: int, level: str | None = None, source: str | None = None, keyword: str | None = None, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LogRecord]:
    query = select(LogRecord).where(LogRecord.run_id == run_id)
    if level: query = query.where(LogRecord.level == level.upper())
    if source: query = query.where(LogRecord.source == source)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at).limit(5000)).all())


@router.get("/logs", response_model=list[LogOut])
def query_logs(log_type: str | None = None, level: str | None = None, trace_id: str | None = None, keyword: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[LogRecord]:
    query = select(LogRecord)
    if user.role == "visitor": query = query.where(LogRecord.log_type.notin_(["command", "access"]))
    if log_type: query = query.where(LogRecord.log_type == log_type)
    if level: query = query.where(LogRecord.level == level.upper())
    if trace_id: query = query.where(LogRecord.trace_id == trace_id)
    if keyword: query = query.where(LogRecord.message.contains(keyword))
    return list(db.scalars(query.order_by(LogRecord.created_at.desc()).limit(1000)).all())


@router.get("/audit-logs", response_model=list[AuditOut])
def list_audit_logs(action: str | None = None, object_type: str | None = None, _: User = Depends(admin_only), db: Session = Depends(get_db)) -> list[AuditLog]:
    query = select(AuditLog)
    if action: query = query.where(AuditLog.action == action)
    if object_type: query = query.where(AuditLog.object_type == object_type)
    return list(db.scalars(query.order_by(AuditLog.created_at.desc()).limit(2000)).all())


@router.get("/audit-logs/export")
def export_audit_logs(request: Request, actor: User = Depends(admin_only), db: Session = Depends(get_db)) -> StreamingResponse:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all(); output = io.StringIO(); writer = csv.writer(output); writer.writerow(["id", "time_utc", "actor_id", "action", "object_type", "object_id", "result", "trace_id"])
    for row in rows: writer.writerow([row.id, row.created_at.isoformat(), row.actor_id, row.action, row.object_type, row.object_id, row.result, row.trace_id])
    write_audit(db, "audit.export", "audit_log", None, actor, request, detail={"count": len(rows)}); db.commit(); return StreamingResponse(iter([output.getvalue().encode("utf-8-sig")]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audit-logs.csv"})


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if not artifact: raise not_found("产物")
    path = Path(artifact.path).resolve(); root = settings.artifact_root.resolve()
    if root not in path.parents or not path.is_file(): raise not_found("产物文件")
    write_audit(db, "artifact.download", "artifact", artifact.id, user, request); db.commit(); return FileResponse(path, media_type=artifact.content_type, filename=artifact.name)


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: int, token: str = Query(...)) -> None:
    try: payload = decode_token(token, "access")
    except jwt.InvalidTokenError: await websocket.close(code=4401); return
    db = SessionLocal()
    try:
        user = db.get(User, int(payload["sub"])); run = load_run(db, run_id)
        if not user or not user.is_active: await websocket.close(code=4401); return
        await websocket.accept(); await websocket.send_json({"type": "snapshot", "status": run.status, "progress": run.progress})
        queue = await broker.subscribe(run_id)
        try:
            while True: await websocket.send_json(await queue.get())
        except WebSocketDisconnect: pass
        finally: broker.unsubscribe(run_id, queue)
    finally: db.close()
