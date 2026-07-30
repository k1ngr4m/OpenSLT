from __future__ import annotations

import hashlib
import json
import time
import typing
from urllib.parse import parse_qs
from uuid import uuid4

from app.core.config import settings
from app.core.logging import redact, step_id_ctx, trace_id_ctx, user_id_ctx
from app.core.observability import emit_observability_event
from app.core.security import decode_token


class BodyCapture:
    def __init__(self, content_type: str = "", *, omit: bool = False) -> None:
        self.content_type = content_type.casefold()
        self.omit = omit
        self.total_bytes = 0
        self._buffer = bytearray()
        self._digest = hashlib.sha256()

    def add(self, payload: bytes) -> None:
        if not payload:
            return
        self.total_bytes += len(payload)
        self._digest.update(payload)
        remaining = settings.observability_body_limit_bytes + 1 - len(self._buffer)
        if remaining > 0:
            self._buffer.extend(payload[:remaining])

    def finish(self) -> typing.Dict[str, typing.Any]:
        result: typing.Dict[str, typing.Any] = {
            "content_type": self.content_type or None,
            "total_bytes": self.total_bytes,
            "sha256": self._digest.hexdigest() if self.total_bytes else None,
            "truncated": self.total_bytes > settings.observability_body_limit_bytes,
        }
        if not self.total_bytes:
            return result
        if self.omit:
            result["omitted_reason"] = "observability_endpoint"
            return result
        if "multipart/form-data" in self.content_type:
            result["omitted_reason"] = "multipart"
            return result
        if not _is_textual(self.content_type):
            result["omitted_reason"] = "binary"
            return result
        if result["truncated"]:
            result["omitted_reason"] = "size_limit"
            return result
        text = bytes(self._buffer).decode("utf-8", errors="replace")
        if "json" in self.content_type:
            try:
                safe_value = redact(json.loads(text))
                result["value"] = safe_value
                result["sha256"] = hashlib.sha256(
                    json.dumps(safe_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                return result
            except (TypeError, ValueError):
                pass
        if "application/x-www-form-urlencoded" in self.content_type:
            safe_value = redact(parse_qs(text, keep_blank_values=True))
            result["value"] = safe_value
            result["sha256"] = hashlib.sha256(
                json.dumps(safe_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            return result
        safe_text = redact(text)
        result["value"] = safe_text
        result["sha256"] = hashlib.sha256(str(safe_text).encode("utf-8")).hexdigest()
        return result


def _is_textual(content_type: str) -> bool:
    return (
        not content_type
        or content_type.startswith("text/")
        or "json" in content_type
        or "xml" in content_type
        or "javascript" in content_type
        or "application/x-www-form-urlencoded" in content_type
    )


def _headers(raw_headers: typing.Iterable[typing.Tuple[bytes, bytes]]) -> typing.Dict[str, typing.Any]:
    grouped: typing.Dict[str, typing.List[str]] = {}
    for raw_key, raw_value in raw_headers:
        key = raw_key.decode("latin-1").casefold()
        grouped.setdefault(key, []).append(raw_value.decode("latin-1", errors="replace"))
    compact: typing.Dict[str, typing.Any] = {
        key: values[0] if len(values) == 1 else values for key, values in grouped.items()
    }
    return typing.cast(typing.Dict[str, typing.Any], redact(compact))


def _query(raw_query: bytes) -> typing.Dict[str, typing.Any]:
    return typing.cast(
        typing.Dict[str, typing.Any],
        redact(parse_qs(raw_query.decode("utf-8", errors="replace"), keep_blank_values=True)),
    )


def _authenticated_user_id(
    raw_headers: typing.Iterable[typing.Tuple[bytes, bytes]]
) -> typing.Optional[int]:
    for raw_key, raw_value in raw_headers:
        if raw_key.lower() != b"authorization":
            continue
        value = raw_value.decode("latin-1", errors="replace")
        if not value.casefold().startswith("bearer "):
            return None
        try:
            return int(decode_token(value.split(" ", 1)[1], "access")["sub"])
        except (KeyError, TypeError, ValueError):
            return None
        except Exception:
            return None
    return None


class ObservabilityMiddleware:
    def __init__(self, app: typing.Callable[..., typing.Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: typing.Dict[str, typing.Any],
        receive: typing.Callable[..., typing.Awaitable[typing.Dict[str, typing.Any]]],
        send: typing.Callable[..., typing.Awaitable[None]],
    ) -> None:
        scope_type = scope.get("type")
        path = str(scope.get("path") or "")
        if scope_type not in {"http", "websocket"} or not (
            path == "/health" or path.startswith(settings.api_v1_prefix)
        ):
            await self.app(scope, receive, send)
            return

        raw_request_headers = scope.get("headers") or []
        request_headers = _headers(raw_request_headers)
        trace_id = str(request_headers.get("x-trace-id") or uuid4())
        trace_token = trace_id_ctx.set(trace_id)
        user_token = user_id_ctx.set(_authenticated_user_id(raw_request_headers))
        step_token = step_id_ctx.set(None)
        try:
            if scope_type == "websocket":
                await self._websocket(scope, receive, send, trace_id)
            else:
                await self._http(scope, receive, send, trace_id, request_headers)
        finally:
            step_id_ctx.reset(step_token)
            user_id_ctx.reset(user_token)
            trace_id_ctx.reset(trace_token)

    async def _http(
        self,
        scope: typing.Dict[str, typing.Any],
        receive: typing.Callable[..., typing.Awaitable[typing.Dict[str, typing.Any]]],
        send: typing.Callable[..., typing.Awaitable[None]],
        trace_id: str,
        request_headers: typing.Dict[str, typing.Any],
    ) -> None:
        started = time.perf_counter()
        path = str(scope.get("path") or "")
        omit_response = path.startswith(settings.api_v1_prefix + "/logs") or path.startswith(
            settings.api_v1_prefix + "/audit-logs"
        )
        request_body = BodyCapture(str(request_headers.get("content-type") or ""))
        response_body = BodyCapture(omit=omit_response)
        response_status = 500
        response_headers: typing.Dict[str, typing.Any] = {}
        result = "failed"
        error_type: typing.Optional[str] = None
        disconnected = False

        async def receive_wrapper() -> typing.Dict[str, typing.Any]:
            nonlocal disconnected
            message = await receive()
            if message.get("type") == "http.request":
                request_body.add(message.get("body") or b"")
            elif message.get("type") == "http.disconnect":
                disconnected = True
            return message

        async def send_wrapper(message: typing.Dict[str, typing.Any]) -> None:
            nonlocal response_status, response_headers, response_body, result
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 500)
                raw_headers = list(message.get("headers") or [])
                if not any(key.lower() == b"x-trace-id" for key, _ in raw_headers):
                    raw_headers.append((b"x-trace-id", trace_id.encode("ascii")))
                    message["headers"] = raw_headers
                response_headers = _headers(raw_headers)
                response_body.content_type = str(response_headers.get("content-type") or "").casefold()
            elif message.get("type") == "http.response.body":
                response_body.add(message.get("body") or b"")
                if not message.get("more_body", False):
                    result = "success" if response_status < 500 else "failed"
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            route = scope.get("route")
            emit_observability_event(
                {
                    "category": "http",
                    "log_type": "access",
                    "event": "http_exchange",
                    "level": "ERROR" if response_status >= 500 or error_type else "INFO",
                    "source": "api",
                    "trace_id": trace_id,
                    "user_id": (scope.get("state") or {}).get(
                        "observability_user_id", user_id_ctx.get()
                    ),
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "result": "disconnected" if disconnected and result != "success" else result,
                    "error_type": error_type,
                    "request": {
                        "method": scope.get("method"),
                        "path": path,
                        "route": getattr(route, "path", None),
                        "query": _query(scope.get("query_string") or b""),
                        "headers": request_headers,
                        "client": (scope.get("client") or [None])[0],
                        "body": request_body.finish(),
                    },
                    "response": {
                        "status": response_status,
                        "headers": response_headers,
                        "body": response_body.finish(),
                    },
                }
            )

    async def _websocket(
        self,
        scope: typing.Dict[str, typing.Any],
        receive: typing.Callable[..., typing.Awaitable[typing.Dict[str, typing.Any]]],
        send: typing.Callable[..., typing.Awaitable[None]],
        trace_id: str,
    ) -> None:
        started = time.perf_counter()
        messages_in = messages_out = bytes_in = bytes_out = 0
        accepted = False
        close_code: typing.Optional[int] = None
        error_type: typing.Optional[str] = None

        async def receive_wrapper() -> typing.Dict[str, typing.Any]:
            nonlocal messages_in, bytes_in, close_code
            message = await receive()
            if message.get("type") == "websocket.receive":
                messages_in += 1
                bytes_in += len(message.get("bytes") or b"") + len(
                    str(message.get("text") or "").encode("utf-8")
                )
            elif message.get("type") == "websocket.disconnect":
                close_code = int(message.get("code") or 1000)
            return message

        async def send_wrapper(message: typing.Dict[str, typing.Any]) -> None:
            nonlocal messages_out, bytes_out, accepted, close_code
            if message.get("type") == "websocket.accept":
                accepted = True
            elif message.get("type") == "websocket.send":
                messages_out += 1
                bytes_out += len(message.get("bytes") or b"") + len(
                    str(message.get("text") or "").encode("utf-8")
                )
            elif message.get("type") == "websocket.close":
                close_code = int(message.get("code") or 1000)
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            emit_observability_event(
                {
                    "category": "websocket",
                    "log_type": "websocket",
                    "event": "websocket_session",
                    "level": "ERROR" if error_type else "INFO",
                    "source": "api",
                    "trace_id": trace_id,
                    "user_id": (scope.get("state") or {}).get(
                        "observability_user_id", user_id_ctx.get()
                    ),
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "result": "accepted" if accepted and not error_type else "rejected",
                    "path": scope.get("path"),
                    "query": _query(scope.get("query_string") or b""),
                    "close_code": close_code,
                    "messages_in": messages_in,
                    "messages_out": messages_out,
                    "bytes_in": bytes_in,
                    "bytes_out": bytes_out,
                    "error_type": error_type,
                }
            )
