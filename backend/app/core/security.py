from __future__ import annotations

import typing
import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.time import beijing_now


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(digest))
    except (ValueError, TypeError):
        return False


def create_token(subject: str, token_type: str, expires_delta: timedelta, **claims: Any) -> str:
    now = beijing_now()
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": secrets.token_urlsafe(24),
        **claims,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, role: str) -> str:
    return create_token(str(user_id), "access", timedelta(minutes=settings.jwt_access_minutes), role=role)


def create_refresh_token(user_id: int) -> str:
    return create_token(str(user_id), "refresh", timedelta(days=settings.jwt_refresh_days))


def decode_token(token: str, expected_type: str) -> typing.Dict[str, Any]:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("unexpected token type")
    return payload


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class CredentialSecretError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise CredentialSecretError("CREDENTIAL_ENCRYPTION_KEY 未配置")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise CredentialSecretError("CREDENTIAL_ENCRYPTION_KEY 格式不合法") from exc


def encrypt_secret(value: typing.Union[str, None]) -> typing.Union[str, None]:
    return _fernet().encrypt(value.encode()).decode() if value else None


def decrypt_secret(value: typing.Union[str, None]) -> typing.Union[str, None]:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CredentialSecretError("资源凭据无法解密，请重新保存资源密码或私钥") from exc
