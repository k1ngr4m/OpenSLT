from __future__ import annotations

import typing

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin_only, get_current_user
from app.api.routes.common import not_found
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import user_id_ctx
from app.core.security import create_access_token, create_refresh_token, decode_token, hash_password, token_fingerprint, verify_password
from app.core.time import beijing_now, from_unix_timestamp
from app.models import RefreshToken, User
from app.schemas import LoginRequest, RefreshRequest, TokenPair, UserCreate, UserOut, UserUpdate
from app.services.audit import write_audit

router = APIRouter()

@router.post("/auth/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        write_audit(db, "login", "user", user.id if user else payload.username, user, request, "failed")
        db.commit()
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误"})
    user_id_ctx.set(user.id)
    request.state.observability_user_id = user.id
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    decoded = decode_token(refresh, "refresh")
    db.add(RefreshToken(user_id=user.id, fingerprint=token_fingerprint(refresh), expires_at=from_unix_timestamp(decoded["exp"])))
    user.last_login_at = beijing_now()
    write_audit(db, "login", "user", user.id, user, request)
    db.commit()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=settings.jwt_access_minutes * 60)


@router.post("/auth/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    try:
        decoded = decode_token(payload.refresh_token, "refresh")
        stored = db.scalar(select(RefreshToken).where(RefreshToken.fingerprint == token_fingerprint(payload.refresh_token)))
        user = db.get(User, int(decoded["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError):
        stored = user = None
    now = beijing_now()
    if not stored or stored.revoked_at or stored.expires_at <= now or not user or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "INVALID_REFRESH_TOKEN", "message": "刷新令牌无效"})
    user_id_ctx.set(user.id)
    request.state.observability_user_id = user.id
    stored.revoked_at = now
    new_refresh = create_refresh_token(user.id)
    new_decoded = decode_token(new_refresh, "refresh")
    db.add(RefreshToken(user_id=user.id, fingerprint=token_fingerprint(new_refresh), expires_at=from_unix_timestamp(new_decoded["exp"])))
    db.commit()
    return TokenPair(access_token=create_access_token(user.id, user.role), refresh_token=new_refresh, expires_in=settings.jwt_access_minutes * 60)


@router.post("/auth/logout", status_code=204)
def logout(payload: RefreshRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    stored = db.scalar(select(RefreshToken).where(RefreshToken.fingerprint == token_fingerprint(payload.refresh_token)))
    if stored and stored.user_id == user.id:
        stored.revoked_at = beijing_now()
    write_audit(db, "logout", "user", user.id, user, request)
    db.commit()
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.get("/users", response_model=typing.List[UserOut])
def list_users(_: User = Depends(admin_only), db: Session = Depends(get_db)) -> typing.List[User]:
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
