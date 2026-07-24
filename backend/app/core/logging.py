from __future__ import annotations

import typing
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

trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|token|secret|authorization|cookie)(\s*[=:]\s*)([^\s,;&]+)"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


def redact(value: Any) -> Any:
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


def add_context(_: Any, __: str, event_dict: typing.Dict[str, Any]) -> typing.Dict[str, Any]:
    event_dict.setdefault("trace_id", trace_id_ctx.get() or str(uuid4()))
    event_dict.setdefault("service", "openslt-api")
    event_dict.setdefault("environment", settings.environment)
    return {key: redact(value) for key, value in event_dict.items()}


def configure_logging() -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        add_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
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
