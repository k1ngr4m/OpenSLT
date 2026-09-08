import json
from io import BytesIO
from urllib.error import HTTPError

from app.services.embedding import EmbeddingClient


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
