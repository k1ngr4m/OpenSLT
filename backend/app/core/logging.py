from __future__ import annotations

import typing
import json
import logging
import re
import sys
from contextvars import ContextVar
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from app.core.config import settings
from app.core.time import beijing_now

trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
user_id_ctx: ContextVar[typing.Union[int, None]] = ContextVar("user_id", default=None)
run_id_ctx: ContextVar[typing.Union[int, None]] = ContextVar("run_id", default=None)
step_id_ctx: ContextVar[typing.Union[int, None]] = ContextVar("step_id", default=None)
sql_logging_suppressed_ctx: ContextVar[bool] = ContextVar(
    "sql_logging_suppressed", default=False
)

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "credential",
    "database_password",
    "jwt",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "set_cookie",
    "set-cookie",
    "token",
    "x_api_key",
    "x-api-key",
}

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|authorization|cookie|api[-_]?key)(\s*[=:]\s*)([^\s,;&]+)"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    result = value
    for pattern in SENSITIVE_PATTERNS:
        def replacement(match: re.Match[str]) -> str:
            groups = match.groups()
            if len(groups) >= 3:
                return f"{groups[0] or ''}{groups[1] or ''}[REDACTED]"
            if len(groups) == 1:
                return f"{groups[0] or ''}[REDACTED]"
            return "[REDACTED]"

        result = pattern.sub(replacement, result)
    return result


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized.startswith("has_") or normalized in {"token_type"}:
        return False
    if normalized in {item.replace("-", "_") for item in SENSITIVE_KEYS}:
        return True
    return normalized.endswith(
        ("_password", "_passwd", "_private_key", "_token", "_secret", "_cookie")
    ) or normalized.startswith(("encrypted_password", "encrypted_private_key"))


def bounded_json(value: Any, limit: int) -> typing.Tuple[Any, bool]:
    safe = redact(value)
    serialized = json.dumps(safe, ensure_ascii=False, default=str, separators=(",", ":"))
    encoded = serialized.encode("utf-8")
    if len(encoded) <= limit:
        return safe, False
    preview = encoded[:limit].decode("utf-8", errors="ignore")
    return {"preview": preview, "original_bytes": len(encoded)}, True


def add_context(_: Any, __: str, event_dict: typing.Dict[str, Any]) -> typing.Dict[str, Any]:
    event_dict.setdefault("trace_id", trace_id_ctx.get() or str(uuid4()))
    event_dict.setdefault("user_id", user_id_ctx.get())
    event_dict.setdefault("run_id", run_id_ctx.get())
    event_dict.setdefault("step_id", step_id_ctx.get())
    event_dict.setdefault("service", "openslt-api")
    event_dict.setdefault("environment", settings.environment)
    return typing.cast(typing.Dict[str, Any], redact(event_dict))


def add_beijing_timestamp(
    _: Any, __: str, event_dict: typing.Dict[str, Any]
) -> typing.Dict[str, Any]:
    event_dict.setdefault("timestamp", beijing_now().isoformat(timespec="milliseconds"))
    return event_dict


def configure_logging() -> None:
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    shared = [
        structlog.contextvars.merge_contextvars,
        add_context,
        add_beijing_timestamp,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(ensure_ascii=False),
        foreign_pre_chain=shared,
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level.upper())
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    file_handler = TimedRotatingFileHandler(
        Path(settings.log_dir) / "application.jsonl", when="midnight", backupCount=14, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()
