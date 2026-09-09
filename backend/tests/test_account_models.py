from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import decrypt_secret
from app.models import AuditLog, SmartCaseGeneration, SvnKnowledgeSource, ModelProvider
from app.services.llm import parse_cases
from app.services.smart_case_generation import execute_smart_case_generation
from test_svn_knowledge_source import _configure_models
from test_model_providers import _create_provider, _create_model

PATH = "/api/v1/model-providers"
CONFIG = {"name": "个人模型", "base_url": "https://personal.example/v1", "api_key": "personal-secret"}


def _configure(client, headers, model_id="personal-model", name="个人模型"):
    provider = client.post(PATH, headers=headers, json={**CONFIG, "name": name})
    assert provider.status_code == 201, provider.text
    model = _create_model(client, headers, provider.json()["id"], "chat", model_id)
    assert client.post(PATH + f"/models/{model['id']}/activate", headers=headers).status_code == 200
    return provider.json(), model


def _tester(client, admin_headers):
    user = client.post("/api/v1/users", headers=admin_headers, json={
        "username": "llm_tester", "password": "tester-password", "display_name": "测试员", "role": "tester",
    })
    assert user.status_code == 201
    token = client.post("/api/v1/auth/login", json={"username": "llm_tester", "password": "tester-password"}).json()["access_token"]
    return user.json()["id"], {"Authorization": f"Bearer {token}"}


def test_account_models_are_private_encrypted_and_embedding_is_admin_only(client, admin_headers, monkeypatch):
    user_id, headers = _tester(client, admin_headers)
    assert client.get(PATH).status_code == 401
    for invalid in ({**CONFIG, "base_url": "http://personal.example/v1"}, {**CONFIG, "name": "  "},
                    {**CONFIG, "base_url": "https://user:secret@example.com/v1"}, {**CONFIG, "user_id": 1}):
        assert client.post(PATH, headers=headers, json=invalid).status_code == 422
    provider, model = _configure(client, headers)
    assert provider["has_api_key"] and "api_key" not in provider
    admin_provider, admin_model = _configure(client, admin_headers, "admin-model")
    assert [p["id"] for p in client.get(PATH, headers=headers).json()] == [provider["id"]]
    assert [p["id"] for p in client.get(PATH, headers=admin_headers).json()] == [admin_provider["id"]]
    for actor, forbidden_provider, forbidden_model in ((headers, admin_provider, admin_model), (admin_headers, provider, model)):
        for method, suffix, body in (("put", f"/{forbidden_provider['id']}", CONFIG),
                                    ("delete", f"/{forbidden_provider['id']}", None),
                                    ("post", f"/{forbidden_provider['id']}/models", {"kind": "chat", "model_id": "x"}),
                                    ("post", f"/{forbidden_provider['id']}/models/discover", {"kind": "chat"}),
                                    ("post", f"/models/{forbidden_model['id']}/activate", None),
                                    ("post", f"/models/{forbidden_model['id']}/connection-test", None),
                                    ("delete", f"/models/{forbidden_model['id']}", None)):
            assert client.request(method, PATH + suffix, headers=actor, json=body).status_code == 404
    embedding = _create_provider(client, admin_headers, kind="embedding")
    embedding_model = _create_model(client, admin_headers, embedding["id"], "embedding", "bge-m3")
    for suffix in ("?kind=embedding", "/models?kind=embedding"):
        assert client.get(PATH + suffix, headers=headers).status_code == 403
    assert client.post(PATH, headers=headers, json={**CONFIG, "kind": "embedding"}).status_code == 403
    for suffix in ("/models", "/models/discover"):
        assert client.post(PATH + f"/{provider['id']}" + suffix, headers=headers,
                           json={"kind": "embedding", "model_id": "x"}).status_code == 403
    assert client.put(PATH + f"/{embedding['id']}", headers=headers, json=CONFIG).status_code == 404
    assert client.post(PATH + f"/models/{embedding_model['id']}/activate", headers=headers).status_code == 404
    assert client.get(PATH + "?kind=embedding", headers=admin_headers).json()[0]["id"] == embedding["id"]
    calls = []
    monkeypatch.setattr("app.api.routes.model_providers.test_llm_connection", lambda *args: calls.append(args))
    assert client.post(PATH + f"/models/{model['id']}/connection-test", headers=headers).status_code == 200
    assert calls == [(CONFIG["base_url"], "personal-model", CONFIG["api_key"])]
    assert client.put(PATH + f"/{provider['id']}", headers=headers, json={**CONFIG, "api_key": None}).json()["has_api_key"]
    with SessionLocal() as db:
        stored = db.get(ModelProvider, provider["id"])
        assert stored.user_id == user_id
        assert stored.encrypted_api_key != CONFIG["api_key"]
        assert decrypt_secret(stored.encrypted_api_key) == CONFIG["api_key"]
        assert all("personal-secret" not in str(item.detail) for item in db.scalars(select(AuditLog)))
    changed = client.put(PATH + f"/{provider['id']}", headers=headers,
                         json={**CONFIG, "base_url": "https://another.example/v1", "api_key": None})
    assert changed.json()["has_api_key"] is False


def test_generation_uses_submitters_snapshot_after_personal_settings_change(client, admin_headers, monkeypatch, tmp_path):
    from app.core.config import settings

    _configure_models(client, admin_headers)
    user_id, headers = _tester(client, admin_headers)
    assert not client.get("/api/v1/chat/status", headers=headers).json()["general_ready"]
    _configure(client, headers)
    with SessionLocal() as db:
        db.add(SvnKnowledgeSource(repository_url="https://svn.example", repository_urls=["https://svn.example"], username="reader", encrypted_password="", last_revisions={"docs": "1"}))
        db.commit()
    requirement = {"source_path": "docs/需求.md", "revision": "1", "requirement_no": "2020", "requirement_name": "需求"}
    monkeypatch.setattr("app.api.routes.smart_cases.published_index_matches", lambda *args: True)
    monkeypatch.setattr("app.api.routes.smart_cases.list_indexed_requirements", lambda: [requirement])
    response = client.post("/api/v1/smart-cases/generations", headers=headers, json={"requirement_path": requirement["source_path"]})
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["llm_model"] == "personal-model"
    assert "encrypted_llm_config" not in task
    assert "personal-secret" not in response.text
    _configure(client, headers, "new-model", "另一个提供商")
    cases = parse_cases('{"cases":[{"title":"登录成功","steps":["登录"],"expected_results":["进入首页"]}]}')
    received = []
    monkeypatch.setattr(settings, "artifact_root", tmp_path)
    monkeypatch.setattr("app.services.smart_case_generation.published_index_matches", lambda *args: True)
    monkeypatch.setattr("app.services.smart_case_generation.get_indexed_document", lambda path: {**requirement, "content": "需求正文"})
    monkeypatch.setattr("app.services.smart_case_generation.EmbeddingClient.embed", lambda *args: [[1.0]])
    monkeypatch.setattr("app.services.smart_case_generation.search_vector_index", lambda *args: [])
    monkeypatch.setattr("app.services.smart_case_generation.generate_cases", lambda llm, *args, **kwargs: received.append((llm.endpoint, llm.model, llm.api_key)) or cases)
    execute_smart_case_generation(task["id"])
    assert received == [(CONFIG["base_url"] + "/chat/completions", "personal-model", CONFIG["api_key"])]
    with SessionLocal() as db:
        generation = db.get(SmartCaseGeneration, task["id"])
        assert generation.created_by == user_id
        assert generation.status == "succeeded"
        assert CONFIG["api_key"] not in generation.encrypted_llm_config
    url = f"/api/v1/smart-cases/generations/{task['id']}"
    assert client.get(url, headers=headers).json()["result_cases"] == cases
    assert client.get(url + "/download", headers=headers).status_code == 200
    assert client.get(url, headers=admin_headers).status_code == 404
    assert client.get(url + "/download", headers=admin_headers).status_code == 404
    assert client.get("/api/v1/smart-cases/generations", headers=admin_headers).json() == []


def test_chat_uses_account_model_without_fallback_or_changing_shared_embedding(client, admin_headers):
    from app.services.chat import chat_status, model_client

    _configure_models(client, admin_headers)
    user_id, headers = _tester(client, admin_headers)
    assert not client.get("/api/v1/chat/status", headers=headers).json()["general_ready"]
    _configure(client, headers)
    with SessionLocal() as db:
        assert chat_status(db, user_id).model == "personal-model"
        assert model_client(db, "chat", user_id).api_key == "personal-secret"
        assert model_client(db, "embedding", user_id).model == "bge-m3"
        assert model_client(db, "chat", 1).model == "qwen3"
    assert client.get("/api/v1/chat/status", headers=headers).json()["model"] == "personal-model"
