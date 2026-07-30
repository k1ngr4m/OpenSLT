from __future__ import annotations

import gzip
import json
import os
import queue
import shutil
import sys
import threading
import typing
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.logging import (
    redact,
    run_id_ctx,
    sql_logging_suppressed_ctx,
    step_id_ctx,
    trace_id_ctx,
    user_id_ctx,
)
from app.core.time import beijing_now


class ObservabilityWriter:
    def __init__(self) -> None:
        self._queue: queue.Queue[typing.Optional[typing.Dict[str, typing.Any]]] = queue.Queue(
            maxsize=settings.observability_queue_size
        )
        self._thread: typing.Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run, name="openslt-observability", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            thread = self._thread
        try:
            self._queue.put(None, timeout=1)
        except queue.Full:
            self._write_emergency({"event": "observability_shutdown_queue_full"})
        if thread:
            thread.join(timeout=timeout)

    def flush(self, timeout: float = 10.0) -> bool:
        marker = threading.Event()
        self.emit(
            {
                "category": "internal",
                "event": "observability_flush",
                "index": False,
                "_flush_marker": marker,
            }
        )
        return marker.wait(timeout)

    def retry_pending(self) -> None:
        self.emit(
            {
                "category": "internal",
                "event": "observability_replay_pending",
                "index": False,
            }
        )

    def emit(self, event: typing.Dict[str, typing.Any]) -> None:
        if not self._running:
            return
        safe_event = typing.cast(typing.Dict[str, typing.Any], redact(event))
        safe_event.setdefault("schema_version", 1)
        safe_event.setdefault("event_id", str(uuid4()))
        safe_event.setdefault("timestamp", beijing_now().isoformat(timespec="milliseconds"))
        safe_event.setdefault("service", "openslt-api")
        safe_event.setdefault("environment", settings.environment)
        safe_event.setdefault("trace_id", trace_id_ctx.get() or str(uuid4()))
        safe_event.setdefault("user_id", user_id_ctx.get())
        safe_event.setdefault("run_id", run_id_ctx.get())
        safe_event.setdefault("step_id", step_id_ctx.get())
        try:
            self._queue.put_nowait(safe_event)
        except queue.Full:
            self._write_emergency(safe_event)

    def _run(self) -> None:
        self._replay_pending()
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    self._replay_pending()
                    return
                marker = item.pop("_flush_marker", None)
                try:
                    if item.get("event") == "observability_replay_pending":
                        self._replay_pending()
                    else:
                        self._process(item)
                finally:
                    if marker:
                        marker.set()
            except Exception as exc:
                self._write_emergency(
                    {
                        "event": "observability_writer_failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            finally:
                self._queue.task_done()

    def _process(self, event: typing.Dict[str, typing.Any]) -> None:
        category = str(event.get("category") or "application")
        path = self._event_path(category, str(event["timestamp"])[:10])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        with path.open("ab") as output:
            offset = output.tell()
            output.write(payload)
            output.flush()
        event["_locator"] = {
            "path": str(path.resolve()),
            "offset": offset,
            "length": len(payload),
        }
        if (
            settings.observability_index_enabled
            and event.get("index", True)
            and category != "internal"
        ):
            try:
                self._persist_index(event)
            except Exception:
                self._append_pending(event)

    def _persist_index(self, event: typing.Dict[str, typing.Any]) -> None:
        from app.core.database import SessionLocal
        from app.models import LogRecord

        locator = typing.cast(typing.Dict[str, typing.Any], event.get("_locator") or {})
        summary = _event_summary(event)
        token = sql_logging_suppressed_ctx.set(True)
        db = SessionLocal()
        try:
            if db.query(LogRecord.id).filter(LogRecord.event_id == event["event_id"]).first():
                return
            detail = {
                key: value
                for key, value in summary.items()
                if key
                not in {
                    "message",
                    "http_method",
                    "http_status",
                    "database_scope",
                    "sql_fingerprint",
                }
            }
            detail.update(
                {
                    "file_offset": locator.get("offset"),
                    "file_length": locator.get("length"),
                }
            )
            db.add(
                LogRecord(
                    event_id=str(event["event_id"]),
                    log_type=str(event.get("log_type") or event.get("category") or "application"),
                    level=str(event.get("level") or "INFO").upper(),
                    event=str(event.get("event") or "observability_event"),
                    message=str(event.get("message") or summary.get("message") or ""),
                    trace_id=str(event.get("trace_id") or ""),
                    user_id=event.get("user_id"),
                    run_id=event.get("run_id"),
                    step_id=event.get("step_id"),
                    source=str(event.get("source") or "api"),
                    duration_ms=_optional_int(event.get("duration_ms")),
                    result=str(event.get("result")) if event.get("result") is not None else None,
                    http_method=summary.get("http_method"),
                    http_status=_optional_int(summary.get("http_status")),
                    database_scope=summary.get("database_scope"),
                    sql_fingerprint=summary.get("sql_fingerprint"),
                    detail=detail,
                    artifact_path=locator.get("path"),
                    is_redacted=True,
                )
            )
            db.commit()
        finally:
            db.close()
            sql_logging_suppressed_ctx.reset(token)

    def _append_pending(self, event: typing.Dict[str, typing.Any]) -> None:
        path = Path(settings.log_dir) / "pending" / "index-pending.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _replay_pending(self) -> None:
        path = Path(settings.log_dir) / "pending" / "index-pending.jsonl"
        if not path.is_file():
            return
        retry_path = path.with_suffix(".retry")
        unresolved: typing.List[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                self._persist_index(json.loads(line))
            except Exception:
                unresolved.append(line)
        if unresolved:
            retry_path.write_text("\n".join(unresolved) + "\n", encoding="utf-8")
            os.replace(str(retry_path), str(path))
        else:
            path.unlink(missing_ok=True)

    def _write_emergency(self, event: typing.Dict[str, typing.Any]) -> None:
        try:
            path = Path(settings.log_dir) / "emergency.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(event)
            payload.setdefault("timestamp", beijing_now().isoformat(timespec="milliseconds"))
            with self._lock, path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(redact(payload), ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            print("OpenSLT observability failure: %s" % exc, file=sys.stderr)

    @staticmethod
    def _event_path(category: str, day: str) -> Path:
        safe_category = category if category in {"http", "sql", "websocket", "internal"} else "application"
        return Path(settings.log_dir) / safe_category / ("%s-%s.jsonl" % (safe_category, day))


def _optional_int(value: typing.Any) -> typing.Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_summary(event: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
    category = event.get("category")
    if category == "http":
        request = typing.cast(typing.Dict[str, typing.Any], event.get("request") or {})
        response = typing.cast(typing.Dict[str, typing.Any], event.get("response") or {})
        method = str(request.get("method") or "")
        path = str(request.get("path") or "")
        status = response.get("status")
        return {
            "message": "%s %s -> %s" % (method, path, status),
            "http_method": method,
            "http_status": status,
            "path": path,
            "route": request.get("route"),
            "request_bytes": (request.get("body") or {}).get("total_bytes"),
            "response_bytes": (response.get("body") or {}).get("total_bytes"),
        }
    if category == "sql":
        operation = str(event.get("operation") or "SQL")
        scope = str(event.get("database_scope") or "")
        return {
            "message": "%s %s (%sms)" % (scope, operation, event.get("duration_ms", 0)),
            "database_scope": scope,
            "sql_fingerprint": event.get("sql_fingerprint"),
            "operation": operation,
            "database": event.get("database"),
            "resource_id": event.get("resource_id"),
            "rowcount": event.get("rowcount"),
            "error_type": event.get("error_type"),
        }
    if category == "websocket":
        return {
            "message": "WebSocket %s (%sms)" % (event.get("result"), event.get("duration_ms", 0)),
            "path": event.get("path"),
            "close_code": event.get("close_code"),
            "messages_in": event.get("messages_in"),
            "messages_out": event.get("messages_out"),
        }
    return {"message": str(event.get("message") or event.get("event") or "")}


writer = ObservabilityWriter()


def emit_observability_event(event: typing.Dict[str, typing.Any]) -> None:
    writer.emit(event)


def read_event_payload(record: typing.Any) -> typing.Dict[str, typing.Any]:
    root = Path(settings.log_dir).resolve()
    path = Path(record.artifact_path or "").resolve()
    if root != path and root not in path.parents:
        raise ValueError("Log payload path is outside LOG_DIR")
    offset = int((record.detail or {}).get("file_offset") or 0)
    length = int((record.detail or {}).get("file_length") or 0)
    if length <= 0 or length > 2_000_000:
        raise ValueError("Invalid log payload length")
    with path.open("rb") as source:
        source.seek(offset)
        payload = source.read(length)
    event = json.loads(payload.decode("utf-8"))
    if event.get("event_id") != record.event_id:
        raise ValueError("Log payload identity mismatch")
    return typing.cast(typing.Dict[str, typing.Any], event)


def archive_observability_files() -> typing.Dict[str, int]:
    now = beijing_now()
    hot_cutoff = (now - timedelta(days=settings.observability_hot_retention_days)).date()
    archive_cutoff = (
        now
        - timedelta(
            days=settings.observability_hot_retention_days
            + settings.observability_archive_retention_days
        )
    ).date()
    archived = 0
    deleted = 0
    root = Path(settings.log_dir)
    archive_root = root / "archive" / "observability"
    for category in ("http", "sql", "websocket"):
        source_dir = root / category
        if source_dir.is_dir():
            for path in source_dir.glob("%s-*.jsonl" % category):
                match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
                if not match:
                    continue
                day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                if day >= hot_cutoff:
                    continue
                target_dir = archive_root / category
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / (path.name + ".gz")
                with path.open("rb") as source, gzip.open(target, "wb") as output:
                    shutil.copyfileobj(source, output)
                path.unlink()
                archived += 1
        target_dir = archive_root / category
        if target_dir.is_dir():
            for path in target_dir.glob("*.jsonl.gz"):
                match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
                if not match:
                    continue
                day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                if day < archive_cutoff:
                    path.unlink()
                    deleted += 1
    return {"files_archived": archived, "archives_deleted": deleted}
