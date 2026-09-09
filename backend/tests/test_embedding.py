import json
from io import BytesIO
from urllib.error import HTTPError

import httpx
import pytest

from app.services.embedding import EmbeddingClient, EmbeddingError, test_embedding_connection as detect_dimensions


def test_embedding_requests_float_vectors(monkeypatch):
    client = EmbeddingClient(
        'https://api-inference.modelscope.cn/v1', 'Qwen/Qwen3-Embedding-0.6B', None,
    )

    def open_request(request, timeout):
        assert request.full_url == 'https://api-inference.modelscope.cn/v1/embeddings'
        body = json.loads(request.data)
        if body.get('encoding_format') != 'float':
            raise HTTPError(request.full_url, 400, 'encoding_format required', {}, None)
        assert body['model'] == 'Qwen/Qwen3-Embedding-0.6B'
        assert body['input'] == ['连接测试', 'second text']
        return BytesIO(json.dumps({'data': [
            {'index': 1, 'embedding': [0.3, 0.4]},
            {'index': 0, 'embedding': [0.1, 0.2]},
        ]}).encode())

    monkeypatch.setattr(client.opener, 'open', open_request)
    assert client.embed(['连接测试', 'second text']) == [[0.1, 0.2], [0.3, 0.4]]


async def test_configured_dimensions_are_checked_in_sync_and_async_requests(monkeypatch):
    raw = json.dumps({'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}).encode()
    client = EmbeddingClient('https://example.com/v1', 'bge-m3', None, expected_dimensions=1024)
    monkeypatch.setattr(client.opener, 'open', lambda *args, **kwargs: BytesIO(raw))
    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    monkeypatch.setattr('app.services.embedding.httpx.AsyncClient', lambda **kwargs: async_client(transport=transport, **kwargs))
    with pytest.raises(EmbeddingError, match='实际返回 2 维.*配置的 1024 维'):
        client.embed(['test'])
    with pytest.raises(EmbeddingError, match='实际返回 2 维.*配置的 1024 维'):
        await client.embed_async(['test'])
    client.expected_dimensions = 2
    assert client.embed(['test']) == await client.embed_async(['test']) == [[0.1, 0.2]]
    monkeypatch.setattr('urllib.request.OpenerDirector.open', lambda *args, **kwargs: BytesIO(raw))
    assert detect_dimensions('https://example.com/v1', 'bge-m3', None) == 2
