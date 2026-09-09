from __future__ import annotations

import asyncio
import json
import sqlite3
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import ActiveAiModel, AiModel, ChatConversation, ChatMessage, ModelProvider, SvnKnowledgeSource, User
from app.services.chat import active_generations, conversation_context, recover_interrupted_chats
from app.services.llm import LlmClient, LlmError


def models():
    with SessionLocal() as db:
        provider = ModelProvider(name="测试模型", base_url="http://model.invalid/v1", allow_insecure_http=True)
        db.add(provider)
        db.flush()
        for kind in ("chat", "embedding"):
            model = AiModel(provider_id=provider.id, kind=kind, model_id="test-" + kind)
            db.add(model)
            db.flush()
            db.add(ActiveAiModel(kind=kind, model_id=model.id))
        db.commit()


def user_headers(client, username, role="tester"):
    with SessionLocal() as db:
        db.add(User(username=username, role=role, password_hash=hash_password("test-password")))
        db.commit()
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "test-password"})
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def conversation(client, headers, mode="general"):
    response = client.post("/api/v1/chat/conversations", headers=headers, json={"mode": mode})
    assert response.status_code == 201, response.text
    return "/api/v1/chat/conversations/%s" % response.json()["id"]


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_chat_stream_history_isolation_and_log_privacy(client, admin_headers, monkeypatch):
    models()
    received = []
    logs = []
    monkeypatch.setattr("app.core.observability_middleware.emit_observability_event", logs.append)

    async def stream(self, messages, max_tokens):
        received.append(messages)
        yield "这是私人的"
        yield "回答"

    monkeypatch.setattr(LlmClient, "stream", stream)
    tester = user_headers(client, "tester")
    visitor = user_headers(client, "visitor", "visitor")
    url = conversation(client, tester)
    first = events(client.post(url + "/messages", headers=tester, json={"content": "我的私人问题 password=secret"}))
    assert [item["type"] for item in first] == ["meta", "delta", "delta", "done"]
    assert first[-1]["message"]["status"] == "completed"
    assert first[-1]["message"]["content"] == "这是私人的回答"
    assert "secret" not in json.dumps(received)
    events(client.post(url + "/messages", headers=tester, json={"content": "继续解释"}))
    assert [item["role"] for item in received[-1]] == ["system", "user", "assistant", "user"]
    assert len(client.get(url + "/messages", headers=tester).json()) == 4
    older = client.get(url + "/messages?limit=2", headers=tester).json()
    assert len(client.get(url + "/messages", params={"before_id": older[0]["id"]}, headers=tester).json()) == 2
    for method, suffix, payload in [("get", "/messages", None), ("post", "/messages", {"content": "越权"}),
                                     ("post", "/cancel", None), ("delete", "", None)]:
        response = client.request(method, url + suffix, headers=admin_headers, json=payload)
        assert response.status_code == 404  # Even admins cannot read another user's chat.
        assert client.request(method, url + suffix, headers=visitor, json=payload).status_code == 403
    assert client.get("/api/v1/chat/conversations", headers=admin_headers).json() == []
    assert client.get("/api/v1/chat/status", headers=visitor).status_code == 403
    serialized = json.dumps(logs, ensure_ascii=False)
    assert "我的私人问题" not in serialized and "这是私人的" not in serialized
    assert "private_chat" in serialized
    assert client.delete(url, headers=tester).status_code == 204
    assert client.get(url + "/messages", headers=tester).status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(ChatMessage)) is None


def test_not_ready_validation_and_failed_answers_are_not_context(client, admin_headers, monkeypatch):
    url = conversation(client, admin_headers)
    assert client.post(url + "/messages", headers=admin_headers, json={"content": "你好"}).status_code == 409
    models()
    for content in ("   ", "x" * 4001):
        assert client.post(url + "/messages", headers=admin_headers, json={"content": content}).status_code == 422
    assert client.post(url + "/messages", headers=admin_headers, json={"content": "你好", "role": "system"}).status_code == 422

    async def broken(self, messages, max_tokens):
        yield "未完成"
        raise LlmError("上游连接中断")

    monkeypatch.setattr(LlmClient, "stream", broken)
    answer = events(client.post(url + "/messages", headers=admin_headers, json={"content": "第一个问题"}))[-1]["message"]
    assert answer["status"] == "failed" and answer["content"] == "未完成"
    with SessionLocal() as db:
        context = conversation_context(db, int(url.rsplit("/", 1)[1]), "第二个问题")
        assert context == [{"role": "user", "content": "第二个问题"}]


def test_knowledge_uses_full_versioned_chunks_and_handles_no_evidence(client, admin_headers, monkeypatch, tmp_path):
    models()
    monkeypatch.setattr(settings, "knowledge_root", tmp_path)
    (tmp_path / "published").mkdir()
    with sqlite3.connect(tmp_path / "published" / "svn-index.sqlite3") as db:
        db.executescript("CREATE TABLE files (source_path TEXT, revision TEXT); CREATE TABLE chunks (source_path TEXT, chunk_no INTEGER, content TEXT, vector BLOB, dimensions INTEGER);")
        db.execute("INSERT INTO files VALUES ('需求/重连.md', '51')")
        db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", ("需求/重连.md", 3, "重连 " * 190 + "必须重新登录", struct.pack("<2f", 1, 0), 2))
    with SessionLocal() as db:
        db.add(SvnKnowledgeSource(repository_url="https://svn.invalid/repo", username="readonly", encrypted_password="unused"))
        db.commit()
    monkeypatch.setattr("app.services.chat.published_index_matches", lambda *args: True)

    async def embed(self, texts):
        return [[1, 0]]

    requests = []

    async def stream(self, messages, max_tokens):
        requests.append(messages)
        yield "重连后必须重新登录。[1]"

    monkeypatch.setattr("app.services.embedding.EmbeddingClient.embed_async", embed)
    monkeypatch.setattr(LlmClient, "stream", stream)
    url = conversation(client, admin_headers, "knowledge")
    output = events(client.post(url + "/messages", headers=admin_headers, json={"content": "重连后怎么办？"}))
    source = output[-1]["message"]["sources"][0]
    assert source["revision"] == "51" and source["chunk_no"] == 3
    assert source["content"].endswith("必须重新登录")  # Beyond the old 500-char preview.
    assert "必须重新登录" in requests[0][-1]["content"]
    assert client.get(url + "/messages", headers=admin_headers).json()[-1]["sources"] == [source]
    monkeypatch.setattr("app.services.chat.published_index_matches", lambda *args: False)
    assert client.post(url + "/messages", headers=admin_headers, json={"content": "再问"}).status_code == 409
    monkeypatch.setattr("app.services.chat.published_index_matches", lambda *args: True)

    async def unrelated(self, texts):
        return [[0, 1]]

    monkeypatch.setattr("app.services.embedding.EmbeddingClient.embed_async", unrelated)
    answer = events(client.post(url + "/messages", headers=admin_headers, json={"content": "其他问题"}))[-1]["message"]
    assert answer["sources"] == [] and "未找到足够相关" in answer["content"]
    assert len(requests) == 1  # No unsupported model answer when retrieval misses.


def test_cancel_closes_generation_and_releases_capacity(client, admin_headers, monkeypatch):
    models()
    started, closed = threading.Event(), threading.Event()

    async def slow(self, messages, max_tokens):
        try:
            yield "部分回答"
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(LlmClient, "stream", slow)
    url = conversation(client, admin_headers)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, url + "/messages", headers=admin_headers, json={"content": "慢问题"})
        assert started.wait(5)
        assert client.post(url + "/messages", headers=admin_headers, json={"content": "重复"}).status_code == 409
        assert client.delete(url, headers=admin_headers).status_code == 409
        other = user_headers(client, "second")
        other_url = conversation(client, other)
        monkeypatch.setattr(settings, "chat_max_concurrent", 1)
        assert client.post(other_url + "/messages", headers=other, json={"content": "超额"}).status_code == 429
        assert client.get("/health").status_code == 200
        assert client.post(url + "/cancel", headers=admin_headers).status_code == 200
        answer = events(response.result(timeout=5))[-1]["message"]
        assert answer["status"] == "cancelled" and answer["content"] == "部分回答"
    assert closed.is_set() and not active_generations
    assert client.get(url + "/messages", headers=admin_headers).json()[-1]["status"] == "cancelled"


def test_restart_marks_abandoned_messages(client, admin_headers):
    url = conversation(client, admin_headers)
    with SessionLocal() as db:
        db.add(ChatMessage(conversation_id=int(url.rsplit("/", 1)[1]), role="assistant", status="running"))
        db.commit()
    recover_interrupted_chats()
    message = client.get(url + "/messages", headers=admin_headers).json()[0]
    assert message["status"] == "cancelled" and "重启" in message["error"]


class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw = raw

    async def __aiter__(self):
        for i in range(0, len(self.raw), 2):
            yield self.raw[i:i + 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("tail, fails", [(b"data: [DONE]\r\n\r\n", False), (b"", True), (b"data: broken\n\n", True)])
async def test_upstream_stream_handles_utf8_crlf_and_incomplete_frames(monkeypatch, tail, fails):
    original = httpx.AsyncClient
    body = ('data: ' + json.dumps({"choices": [{"delta": {"content": "你好"}}]}, ensure_ascii=False) + '\r\n\r\n').encode() + tail

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=FragmentedStream(body))

    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    chunks = []
    async def consume():
        async for chunk in LlmClient("https://model.invalid/v1", "model", None).stream([], 100):
            chunks.append(chunk)
    if fails:
        with pytest.raises(LlmError):
            await consume()
    else:
        await consume()
    assert chunks == ["你好"]


@pytest.mark.asyncio
async def test_upstream_redirect_does_not_forward_credentials(monkeypatch):
    original = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://elsewhere.invalid"})
    monkeypatch.setattr("app.services.llm.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(LlmError, match="重定向"):
        async for _ in LlmClient("https://model.invalid/v1", "model", "private-key").stream([], 100):
            pass
    assert len(requests) == 1
