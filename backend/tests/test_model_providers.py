from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import ActiveAiModel, ModelProvider, SvnKnowledgeSource, UserChatModel
from app.core.security import decrypt_secret
from app.core.time import beijing_now


def _create_provider(client, headers, name="内网模型", api_key="model-secret", kind="chat"):
    response = client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={
            "name": name,
            "kind": kind,
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
    embedding_provider = _create_provider(client, admin_headers, kind="embedding")
    embedding = _create_model(client, admin_headers, embedding_provider["id"], "embedding", "bge-m3")
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
    assert [item["model_id"] for item in models if item["is_active"]] == ["qwen3-32b"]
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
            "embedding",
        }
        assert db.scalar(select(UserChatModel)).model_id == second["id"]
    finally:
        db.close()


def test_discovery_and_connection_tests_are_independent(client, admin_headers, monkeypatch) -> None:
    provider = _create_provider(client, admin_headers)
    chat = _create_model(client, admin_headers, provider["id"], "chat", "qwen3")
    embedding_provider = _create_provider(client, admin_headers, kind="embedding")
    embedding = _create_model(client, admin_headers, embedding_provider["id"], "embedding", "bge-m3")
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
    embedding_provider = _create_provider(client, admin_headers, kind="embedding")
    embedding = _create_model(client, admin_headers, embedding_provider["id"], "embedding", "bge-m3")
    assert client.post(
        f"/api/v1/model-providers/models/{embedding['id']}/activate", headers=admin_headers
    ).status_code == 200
    db = SessionLocal()
    try:
        assert db.scalar(select(SvnKnowledgeSource)).sync_status == "stale"
    finally:
        db.close()


def test_embedding_dimensions_are_saved_validated_and_invalidate_index(client, admin_headers, monkeypatch):
    path = '/api/v1/model-providers'
    chat = _create_provider(client, admin_headers)
    provider = _create_provider(client, admin_headers, kind='embedding')
    assert chat['embedding_dimensions'] is None
    assert provider['embedding_dimensions'] == 1024
    assert [item['id'] for item in client.get(path, headers=admin_headers).json()] == [chat['id']]
    assert [item['id'] for item in client.get(path + '?kind=embedding', headers=admin_headers).json()] == [provider['id']]
    for provider_id, kind in [(chat['id'], 'embedding'), (provider['id'], 'chat')]:
        assert client.post(f'{path}/{provider_id}/models', headers=admin_headers,
                           json={'kind': kind, 'model_id': 'wrong-kind'}).status_code == 422
    model = _create_model(client, admin_headers, provider['id'], 'embedding', 'bge-m3')
    assert client.post(f"{path}/models/{model['id']}/activate", headers=admin_headers).status_code == 200
    with SessionLocal() as db:
        db.add(SvnKnowledgeSource(repository_url='https://svn.example/repo', username='', encrypted_password='',
                                  last_success_at=beijing_now(), sync_status='succeeded'))
        db.commit()
    body = {'name': provider['name'], 'base_url': provider['base_url'], 'allow_insecure_http': True}
    url = f"{path}/{provider['id']}"
    for value in [0, -1, 1.5, None, True, '512', 2147483648]:
        assert client.put(url, headers=admin_headers, json={**body, 'embedding_dimensions': value}).status_code == 422
    monkeypatch.setattr('app.api.routes.model_providers.test_embedding_connection', lambda *args: 768)
    detected = client.post(f"{path}/models/{model['id']}/connection-test", headers=admin_headers)
    assert detected.json()['dimensions'] == 768
    assert client.get(path + '?kind=embedding', headers=admin_headers).json()[0]['embedding_dimensions'] == 1024
    monkeypatch.setattr('app.api.routes.model_providers.active_svn_task', lambda db: True)
    assert client.put(url, headers=admin_headers, json={**body, 'embedding_dimensions': 768}).status_code == 409
    monkeypatch.setattr('app.api.routes.model_providers.active_svn_task', lambda db: None)
    updated = client.put(url, headers=admin_headers, json={**body, 'embedding_dimensions': 768})
    assert updated.status_code == 200, updated.text
    assert updated.json()['embedding_dimensions'] == 768
    assert client.put(url, headers=admin_headers, json=body).json()['embedding_dimensions'] == 768
    with SessionLocal() as db:
        assert db.get(ModelProvider, provider['id']).embedding_dimensions == 768
        assert db.scalar(select(SvnKnowledgeSource)).sync_status == 'stale'
