from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import decrypt_secret
from app.models import AuditLog, SmartCaseGeneration, SvnKnowledgeSource, UserLlmConfig
from app.services.llm import parse_cases
from app.services.smart_case_generation import execute_smart_case_generation
from test_svn_knowledge_source import _configure_models

PATH = "/api/v1/model-providers/personal-llm"
CONFIG = {"base_url": "https://personal.example/v1", "model_id": "personal-model", "api_key": "personal-secret"}


def _tester(client, admin_headers):
    user = client.post("/api/v1/users", headers=admin_headers, json={
        "username": "llm_tester", "password": "tester-password", "display_name": "测试员", "role": "tester",
    })
    assert user.status_code == 201
    token = client.post("/api/v1/auth/login", json={"username": "llm_tester", "password": "tester-password"}).json()["access_token"]
    return user.json()["id"], {"Authorization": f"Bearer {token}"}


def test_personal_config_is_private_encrypted_and_validated(client, admin_headers, monkeypatch):
    user_id, headers = _tester(client, admin_headers)
    assert client.get(PATH).status_code == 401
    for invalid in ({**CONFIG, "base_url": "http://personal.example/v1"}, {**CONFIG, "model_id": "  "}, {**CONFIG, "base_url": "https://user:secret@example.com/v1"}, {**CONFIG, "user_id": 1}):
        assert client.put(PATH, headers=headers, json=invalid).status_code == 422
    saved = client.put(PATH, headers=headers, json=CONFIG)
    assert saved.status_code == 200
    assert saved.json()["has_api_key"]
    assert "personal-secret" not in saved.text
    assert "api_key" not in saved.json()
    assert client.get(PATH, headers=admin_headers).json()["configured"] is False
    assert client.put(PATH, headers=admin_headers, json={**CONFIG, "model_id": "admin-model"}).status_code == 200
    assert client.get(PATH, headers=headers).json()["model_id"] == "personal-model"
    calls = []
    monkeypatch.setattr("app.api.routes.model_providers.test_llm_connection", lambda *args: calls.append(args))
    assert client.post(PATH + "/connection-test", headers=headers).status_code == 200
    assert calls == [(CONFIG["base_url"], CONFIG["model_id"], CONFIG["api_key"])]
    assert client.put(PATH, headers=headers, json={**CONFIG, "api_key": None}).json()["has_api_key"]
    with SessionLocal() as db:
        stored = db.get(UserLlmConfig, user_id)
        assert stored.encrypted_api_key != CONFIG["api_key"]
        assert decrypt_secret(stored.encrypted_api_key) == CONFIG["api_key"]
        assert all("personal-secret" not in str(item.detail) for item in db.scalars(select(AuditLog)))
    changed = client.put(PATH, headers=headers, json={**CONFIG, "base_url": "https://another.example/v1", "api_key": None})
    assert changed.json()["has_api_key"] is False
    assert client.delete(PATH, headers=headers).status_code == 204
    assert not client.get(PATH, headers=headers).json()["configured"]
    assert client.get(PATH, headers=admin_headers).json()["configured"]


def test_generation_uses_submitters_snapshot_after_personal_settings_change(client, admin_headers, monkeypatch, tmp_path):
    from app.core.config import settings

    _configure_models(client, admin_headers)
    user_id, headers = _tester(client, admin_headers)
    assert client.get(PATH, headers=headers).json()["default_model"] == "qwen3"
    assert client.put(PATH, headers=headers, json=CONFIG).status_code == 200
    with SessionLocal() as db:
        db.add(SvnKnowledgeSource(repository_url="https://svn.example", repository_urls=["https://svn.example"], username="reader", encrypted_password="", last_revisions={"docs": "1"}))
        db.commit()
    requirement = {"source_path": "docs/需求.md", "revision": "1", "requirement_no": "2020", "requirement_name": "需求"}
    monkeypatch.setattr("app.api.routes.smart_cases.published_index_matches", lambda *args: True)
    monkeypatch.setattr("app.api.routes.smart_cases.list_indexed_requirements", lambda: [requirement])
    response = client.post("/api/v1/smart-cases/generations", headers=headers, json={"requirement_path": requirement["source_path"]})
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["llm_model"] == CONFIG["model_id"]
    assert "encrypted_llm_config" not in task
    assert "personal-secret" not in response.text
    assert client.put(PATH, headers=headers, json={**CONFIG, "model_id": "new-model", "base_url": "https://new.example/v1", "api_key": "new-secret"}).status_code == 200
    assert client.delete(PATH, headers=headers).status_code == 204
    cases = parse_cases('{"cases":[{"title":"登录成功","steps":["登录"],"expected_results":["进入首页"]}]}')
    received = []
    monkeypatch.setattr(settings, "artifact_root", tmp_path)
    monkeypatch.setattr("app.services.smart_case_generation.published_index_matches", lambda *args: True)
    monkeypatch.setattr("app.services.smart_case_generation.get_indexed_document", lambda path: {**requirement, "content": "需求正文"})
    monkeypatch.setattr("app.services.smart_case_generation.EmbeddingClient.embed", lambda *args: [[1.0]])
    monkeypatch.setattr("app.services.smart_case_generation.search_vector_index", lambda *args: [])
    monkeypatch.setattr("app.services.smart_case_generation.generate_cases", lambda llm, *args, **kwargs: received.append((llm.endpoint, llm.model, llm.api_key)) or cases)
    execute_smart_case_generation(task["id"])
    assert received == [(CONFIG["base_url"] + "/chat/completions", CONFIG["model_id"], CONFIG["api_key"])]
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


def test_chat_uses_personal_llm_without_changing_shared_embedding(client, admin_headers):
    from app.services.chat import chat_status, model_client

    _configure_models(client, admin_headers)
    user_id, headers = _tester(client, admin_headers)
    with SessionLocal() as db:
        assert model_client(db, "chat", user_id).model == "qwen3"
    assert client.put(PATH, headers=headers, json=CONFIG).status_code == 200
    with SessionLocal() as db:
        assert chat_status(db, user_id).model == "personal-model"
        assert model_client(db, "chat", user_id).api_key == "personal-secret"
        assert model_client(db, "embedding", user_id).model == "bge-m3"
        assert model_client(db, "chat").model == "qwen3"
    assert client.get("/api/v1/chat/status", headers=headers).json()["model"] == "personal-model"
