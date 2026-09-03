from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import redact
from app.models import AuditLog, DurableTask, SvnKnowledgeSource
from app.services.svn_knowledge import SvnClient, _build_manifest, _publish_vector_index, normalize_include_paths, search_vector_index
from app.services.svn_knowledge import enqueue_due_svn_syncs
from app.core.time import beijing_now


def _payload(**overrides):
    value = {
        "repository_url": "http://svn.intranet.example/svn/knowledge",
        "username": "openslt-readonly",
        "password": "svn-secret-value",
        "embedding_base_url": "http://embedding.intranet.example/v1",
        "embedding_model": "bge-m3",
        "embedding_api_key": "embedding-secret-value",
        "allow_insecure_embedding_http": True,
        "include_paths": ["docs/测试文档", "docs/需求文档"],
        "sync_interval_minutes": 30,
        "enabled": True,
        "allow_insecure_http": True,
    }
    value.update(overrides)
    return value


def test_svn_config_validates_scope_and_never_returns_or_queues_password(client, admin_headers) -> None:
    assert redact({"embedding_api_key": "secret"}) == {"embedding_api_key": "[REDACTED]"}
    refused = client.put(
        "/api/v1/smart-cases/knowledge-source",
        headers=admin_headers,
        json=_payload(allow_insecure_http=False),
    )
    assert refused.status_code == 422
    assert normalize_include_paths(["docs/tests", "docs/tests"]) == ["docs/tests"]
    for invalid in ["", "/absolute", "../outside", "docs/.svn/text"]:
        response = client.put(
            "/api/v1/smart-cases/knowledge-source",
            headers=admin_headers,
            json=_payload(include_paths=[invalid]),
        )
        assert response.status_code == 422

    saved = client.put(
        "/api/v1/smart-cases/knowledge-source",
        headers=admin_headers,
        json=_payload(),
    )
    assert saved.status_code == 200
    assert saved.json()["has_password"] is True
    assert saved.json()["has_embedding_api_key"] is True
    assert "password" not in saved.text.replace("has_password", "")
    fetched = client.get("/api/v1/smart-cases/knowledge-source", headers=admin_headers)
    assert "svn-secret-value" not in fetched.text
    assert "embedding-secret-value" not in fetched.text

    first = client.post("/api/v1/smart-cases/knowledge-source/sync", headers=admin_headers)
    second = client.post("/api/v1/smart-cases/knowledge-source/sync", headers=admin_headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert second.json()["reused"] is True

    db = SessionLocal()
    try:
        source = db.scalar(select(SvnKnowledgeSource))
        task = db.scalar(select(DurableTask).where(DurableTask.task_type == "svn_sync"))
        audits = list(db.scalars(select(AuditLog)).all())
        assert source is not None and source.encrypted_password != "svn-secret-value"
        assert source.encrypted_embedding_api_key != "embedding-secret-value"
        assert task is not None and task.payload == {"source_id": source.id, "reason": "manual"}
        serialized = json.dumps([item.detail for item in audits], ensure_ascii=False)
        assert "svn-secret-value" not in serialized
        assert "embedding-secret-value" not in serialized
    finally:
        db.close()


def test_connection_test_reuses_saved_password_without_exposing_it(client, admin_headers, monkeypatch) -> None:
    assert client.put(
        "/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload()
    ).status_code == 200
    received = {}

    def fake_test(repository_url, username, password, include_paths):
        received.update(password=password, paths=include_paths)
        return {"ok": True, "svn_version": "1.10.0", "checked_paths": include_paths}

    monkeypatch.setattr("app.api.routes.smart_cases.test_svn_connection", fake_test)
    monkeypatch.setattr("app.api.routes.smart_cases.test_embedding_connection", lambda *args: 1024)
    tested = client.post(
        "/api/v1/smart-cases/knowledge-source/connection-test",
        headers=admin_headers,
        json=_payload(password=None),
    )
    assert tested.status_code == 200
    assert tested.json()["checked_paths"] == ["docs/测试文档", "docs/需求文档"]
    assert tested.json()["embedding_dimensions"] == 1024
    assert received["password"] == "svn-secret-value"
    assert "svn-secret-value" not in tested.text


def test_manifest_is_incremental_excludes_svn_and_keeps_source_revisions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "knowledge_root", tmp_path)
    working_copy = tmp_path / "wc"
    (working_copy / ".svn").mkdir(parents=True)
    (working_copy / ".svn" / "entries").write_text("private", encoding="utf-8")
    (working_copy / "cases").mkdir()
    document = working_copy / "cases" / "用例.md"
    document.write_text("first", encoding="utf-8")
    first, first_changes = _build_manifest(
        "http://svn.example/repo", {"docs/tests": "41"}, {"docs/tests": working_copy}, {}
    )
    second, second_changes = _build_manifest(
        "http://svn.example/repo", {"docs/tests": "42"}, {"docs/tests": working_copy}, first
    )
    assert list(second["files"]) == ["docs/tests/cases/用例.md"]
    assert first_changes["added"] == 1
    assert second_changes == {"added": 0, "changed": 0, "deleted": 0, "unchanged": 1}
    assert second["revisions"] == {"docs/tests": "42"}

    class FakeEmbedding:
        base_url = "http://embedding.example/v1"
        model = "bge-m3"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    dimensions = _publish_vector_index(first, {}, {"docs/tests": working_copy}, FakeEmbedding())
    results = search_vector_index("first", [1.0, 0.0], 5)
    assert dimensions == 2
    assert results[0]["source_path"] == "docs/tests/cases/用例.md"
    assert results[0]["revision"] == "41"


def test_svn_password_only_enters_the_non_echoing_pty(tmp_path: Path) -> None:
    executable = tmp_path / "fake-svn"
    secret = "unique-pty-secret-92841"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import os, sys\n"
        "secret = 'unique-pty-secret-92841'\n"
        "if secret in ' '.join(sys.argv) or secret in '\\n'.join(os.environ.values()): sys.exit(9)\n"
        "print(\"Password for 'readonly': \", end='', flush=True)\n"
        "if sys.stdin.readline().strip() != secret: sys.exit(8)\n"
        "print('<?xml version=\"1.0\"?><info/>')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    output = SvnClient(str(executable), timeout_seconds=5).run(["info", "--xml", "http://svn/repo"], "readonly", secret)
    assert "<info/>" in output
    assert secret not in output


def test_scheduled_sync_is_idempotent_per_thirty_minute_window(client, admin_headers) -> None:
    assert client.put(
        "/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload()
    ).status_code == 200
    now = beijing_now().replace(minute=1, second=0, microsecond=0)
    db = SessionLocal()
    try:
        first = enqueue_due_svn_syncs(db, now)
        assert first is not None
        first.status = "succeeded"
        db.commit()
        same_window = enqueue_due_svn_syncs(db, now + timedelta(minutes=10))
        assert same_window is not None and same_window.id == first.id
        next_window = enqueue_due_svn_syncs(db, now + timedelta(minutes=31))
        assert next_window is not None and next_window.id != first.id
        assert next_window.status == "queued"
    finally:
        db.close()
