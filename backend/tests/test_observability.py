from __future__ import annotations

import json
import typing
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import redact, sql_logging_suppressed_ctx
from app.core.observability import archive_observability_files, writer
from app.core.observability_middleware import BodyCapture
from app.core.sql_observability import sql_fingerprint, sql_template
from app.core.time import beijing_now
from app.models import LogRecord


def test_recursive_redaction_and_sql_template() -> None:
    value = {
        "password": "plain",
        "nested": [{"access_token": "jwt-value", "token_type": "bearer"}],
        "authorization": "Bearer abc.def.ghi",
        "has_database_password": True,
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    }
    safe = redact(value)
    assert safe["password"] == "[REDACTED]"
    assert safe["nested"][0] == {"access_token": "[REDACTED]", "token_type": "bearer"}
    assert safe["authorization"] == "[REDACTED]"
    assert safe["has_database_password"] is True
    assert safe["private_key"] == "[REDACTED]"

    template = sql_template("SELECT * FROM users WHERE password = 'secret' AND id = 42")
    assert "secret" not in template
    assert "42" not in template
    assert "[REDACTED]" in template
    assert template.count("?") == 1
    assert len(sql_fingerprint(template)) == 64


def test_body_capture_redacts_json_and_omits_large_payload() -> None:
    body = BodyCapture("application/json")
    body.add(b'{"password":"secret","token_type":"bearer"}')
    result = body.finish()
    assert result["value"] == {"password": "[REDACTED]", "token_type": "bearer"}
    assert "secret" not in json.dumps(result)

    large = BodyCapture("application/json")
    large.add(b"x" * (settings.observability_body_limit_bytes + 1))
    result = large.finish()
    assert result["truncated"] is True
    assert result["omitted_reason"] == "size_limit"
    assert "value" not in result


def test_http_sql_index_search_detail_and_permissions(
    client: TestClient, admin_headers: typing.Dict[str, str], monkeypatch: typing.Any
) -> None:
    monkeypatch.setattr(settings, "observability_index_enabled", True)
    trace_id = "observability-test-trace"
    response = client.post(
        "/api/v1/resources",
        headers={**admin_headers, "X-Trace-ID": trace_id},
        json={
            "name": "日志资源",
            "resource_type": "rem",
            "business_code": "fut_mm",
            "host": "127.0.0.1",
            "ssh_port": 22,
            "username": "tester",
            "auth_type": "password",
            "password": "request-secret",
            "remote_path": "/tmp/openslt",
            "capabilities": {},
            "trade_ip": "127.0.0.2",
            "trade_tcp_port": 10001,
            "trade_udp_port": 10002,
            "query_ip": "127.0.0.3",
            "query_port": 10003,
            "version_info": "test",
            "notes": "",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.headers["x-trace-id"] == trace_id
    assert writer.flush()

    search = client.get(
        "/api/v1/logs/search",
        headers=admin_headers,
        params={"group": "access", "trace_id": trace_id, "page_size": 20},
    )
    assert search.status_code == 200, search.text
    page = search.json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["http_method"] == "POST"
    assert item["http_status"] == 201
    assert item["user_id"] == 1

    detail = client.get(f"/api/v1/logs/{item['event_id']}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()["payload"]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["request"]["body"]["value"]["password"] == "[REDACTED]"
    assert "request-secret" not in serialized
    assert payload["trace_id"] == trace_id

    assert writer.flush()
    with SessionLocal() as db:
        sql_rows = list(
            db.scalars(
                select(LogRecord).where(
                    LogRecord.trace_id == trace_id,
                    LogRecord.log_type == "sql",
                )
            ).all()
        )
        assert sql_rows
        assert all(row.database_scope == "platform" for row in sql_rows)
        assert not db.scalar(
            select(LogRecord).where(LogRecord.message.contains("t_log_records"))
        )

    visitor = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "username": "logvisitor",
            "display_name": "日志访客",
            "password": "visitor-password",
            "role": "visitor",
        },
    )
    assert visitor.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "logvisitor", "password": "visitor-password"},
    ).json()
    visitor_headers = {"Authorization": f"Bearer {login['access_token']}"}
    hidden = client.get(
        "/api/v1/logs/search", headers=visitor_headers, params={"group": "access"}
    )
    assert hidden.status_code == 200
    assert hidden.json()["total"] == 0
    assert client.get(f"/api/v1/logs/{item['event_id']}", headers=visitor_headers).status_code == 403


def test_observability_file_retention(monkeypatch: typing.Any, tmp_path: Path) -> None:
    original_log_dir = settings.log_dir
    monkeypatch.setattr(settings, "log_dir", tmp_path)
    try:
        old_hot_day = (
            beijing_now() - timedelta(days=settings.observability_hot_retention_days + 1)
        ).strftime("%Y-%m-%d")
        source = tmp_path / "http" / f"http-{old_hot_day}.jsonl"
        source.parent.mkdir(parents=True)
        source.write_text("{}\n", encoding="utf-8")

        expired_day = (
            beijing_now()
            - timedelta(
                days=settings.observability_hot_retention_days
                + settings.observability_archive_retention_days
                + 1
            )
        ).strftime("%Y-%m-%d")
        expired = (
            tmp_path
            / "archive"
            / "observability"
            / "sql"
            / f"sql-{expired_day}.jsonl.gz"
        )
        expired.parent.mkdir(parents=True)
        expired.write_bytes(b"expired")

        result = archive_observability_files()
        assert result == {"files_archived": 1, "archives_deleted": 1}
        assert not source.exists()
        assert (tmp_path / "archive" / "observability" / "http" / f"http-{old_hot_day}.jsonl.gz").is_file()
        assert not expired.exists()
    finally:
        monkeypatch.setattr(settings, "log_dir", original_log_dir)


def test_sql_logging_suppression_context() -> None:
    token = sql_logging_suppressed_ctx.set(True)
    try:
        assert sql_logging_suppressed_ctx.get() is True
    finally:
        sql_logging_suppressed_ctx.reset(token)


def test_resource_database_proxy_records_cursor_and_transaction(monkeypatch: typing.Any) -> None:
    import app.core.sql_observability as sql_observability

    captured: typing.List[typing.Dict[str, typing.Any]] = []

    class FakeCursor:
        rowcount = 3

        def execute(self, query: str, args: typing.Any = None) -> int:
            assert query == "SELECT * FROM orders WHERE account = %s"
            assert args == ("sensitive-account",)
            return 3

    class FakeConnection:
        committed = False

        def cursor(self, cursor_class: typing.Any = None) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            self.committed = True

    monkeypatch.setattr(
        sql_observability,
        "_emit_sql",
        lambda **kwargs: captured.append(kwargs),
    )
    raw = FakeConnection()
    connection = sql_observability.LoggingConnectionProxy(raw, 7, "orders")
    connection.cursor().execute(
        "SELECT * FROM orders WHERE account = %s", ("sensitive-account",)
    )
    connection.commit()

    assert raw.committed is True
    assert captured[0]["database_scope"] == "resource"
    assert captured[0]["resource_id"] == 7
    assert captured[0]["database"] == "orders"
    assert captured[0]["rowcount"] == 3
    assert captured[1]["statement"] == "COMMIT"
