from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import ActiveAiModel, ModelProvider, SvnKnowledgeSource
from app.core.security import decrypt_secret
from app.core.time import beijing_now


def _create_provider(client, headers, name="内网模型", api_key="model-secret"):
    response = client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={
            "name": name,
            "base_url": "http://models.intranet.example/v1",
            "api_key": api_key,
            "allow_insecure_http": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_model(client, headers, provider_id, kind, model_id):
    response = client.post(
        f"/api/v1/model-providers/{provider_id}/models",
        headers=headers,
        json={"kind": kind, "model_id": model_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_provider_models_are_write_only_and_have_one_active_per_kind(client, admin_headers) -> None:
    refused = client.post(
        "/api/v1/model-providers",
        headers=admin_headers,
        json={"name": "不安全", "base_url": "http://models.example/v1"},
    )
    assert refused.status_code == 422

    provider = _create_provider(client, admin_headers)
    first = _create_model(client, admin_headers, provider["id"], "chat", "qwen3")
    second = _create_model(client, admin_headers, provider["id"], "chat", "qwen3-32b")
    embedding = _create_model(client, admin_headers, provider["id"], "embedding", "bge-m3")
    assert client.post(
        f"/api/v1/model-providers/models/{first['id']}/activate", headers=admin_headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/model-providers/models/{second['id']}/activate", headers=admin_headers
    ).status_code == 200
    assert client.post(
        f"/api/v1/model-providers/models/{embedding['id']}/activate", headers=admin_headers
    ).status_code == 200

    listed = client.get("/api/v1/model-providers", headers=admin_headers)
    assert listed.status_code == 200
    assert "model-secret" not in listed.text
    models = listed.json()[0]["models"]
    assert [item["model_id"] for item in models if item["is_active"]] == ["qwen3-32b", "bge-m3"]
    chat_models = client.get(
        "/api/v1/model-providers/models?kind=chat", headers=admin_headers
    )
    assert [item["model_id"] for item in chat_models.json()] == ["qwen3", "qwen3-32b"]
    assert all(item["kind"] == "chat" for item in chat_models.json())
    assert client.delete(
        f"/api/v1/model-providers/models/{second['id']}", headers=admin_headers
    ).status_code == 409
    assert client.delete(
        f"/api/v1/model-providers/{provider['id']}", headers=admin_headers
    ).status_code == 409

    db = SessionLocal()
    try:
        stored = db.scalar(select(ModelProvider))
        assert stored.encrypted_api_key != "model-secret"
        assert decrypt_secret(stored.encrypted_api_key) == "model-secret"
        assert {item.kind for item in db.scalars(select(ActiveAiModel)).all()} == {
            "chat",
            "embedding",
        }
    finally:
        db.close()


def test_discovery_and_connection_tests_are_independent(client, admin_headers, monkeypatch) -> None:
    provider = _create_provider(client, admin_headers)
    chat = _create_model(client, admin_headers, provider["id"], "chat", "qwen3")
    embedding = _create_model(client, admin_headers, provider["id"], "embedding", "bge-m3")
    calls = []

    monkeypatch.setattr(
        "app.api.routes.model_providers.list_provider_models",
        lambda base_url, api_key: ["bge-m3", "qwen3"],
    )
    monkeypatch.setattr(
        "app.api.routes.model_providers.test_llm_connection",
        lambda base_url, model_id, api_key: calls.append(("chat", model_id)),
    )
    monkeypatch.setattr(
        "app.api.routes.model_providers.test_embedding_connection",
        lambda base_url, model_id, api_key: calls.append(("embedding", model_id)) or 1024,
    )

    discovered = client.post(
        f"/api/v1/model-providers/{provider['id']}/models/discover",
        headers=admin_headers,
        json={"kind": "chat"},
    )
    assert discovered.status_code == 200
    assert discovered.json()["models"] == ["bge-m3", "qwen3"]
    chat_test = client.post(
        f"/api/v1/model-providers/models/{chat['id']}/connection-test",
        headers=admin_headers,
    )
    embedding_test = client.post(
        f"/api/v1/model-providers/models/{embedding['id']}/connection-test",
        headers=admin_headers,
    )
    assert chat_test.json() == {
        "ok": True,
        "kind": "chat",
        "model_id": "qwen3",
        "dimensions": None,
    }
    assert embedding_test.json()["dimensions"] == 1024
    assert calls == [("chat", "qwen3"), ("embedding", "bge-m3")]


def test_activating_embedding_marks_a_published_index_stale(client, admin_headers) -> None:
    assert client.put(
        "/api/v1/smart-cases/knowledge-source",
        headers=admin_headers,
        json={
            "repository_urls": ["https://svn.example/knowledge"],
            "username": "readonly",
            "password": "secret",
            "include_paths": ["docs"],
            "sync_interval_minutes": 30,
            "enabled": True,
            "allow_insecure_http": False,
        },
    ).status_code == 200
    db = SessionLocal()
    try:
        source = db.scalar(select(SvnKnowledgeSource))
        source.last_success_at = beijing_now()
        source.sync_status = "succeeded"
        db.commit()
    finally:
        db.close()

    provider = _create_provider(client, admin_headers)
    embedding = _create_model(client, admin_headers, provider["id"], "embedding", "bge-m3")
    assert client.post(
        f"/api/v1/model-providers/models/{embedding['id']}/activate", headers=admin_headers
    ).status_code == 200
    db = SessionLocal()
    try:
        assert db.scalar(select(SvnKnowledgeSource)).sync_status == "stale"
    finally:
        db.close()
