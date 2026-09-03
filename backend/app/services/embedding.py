from __future__ import annotations

import json
import math
import typing
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class EmbeddingError(RuntimeError):
    pass


def normalize_embedding_base_url(value: str, allow_insecure_http: bool) -> str:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Embedding Base URL 必须是完整的 HTTP 或 HTTPS URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Embedding Base URL 不得包含凭据、查询参数或片段")
    if parts.scheme == "http" and not allow_insecure_http:
        raise ValueError("Embedding HTTP 会明文传输 API Key 和资料，必须由管理员显式允许")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise EmbeddingError("Embedding 服务返回了重定向，已拒绝向其他地址发送资料")


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, api_key: typing.Optional[str], timeout_seconds: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = self.base_url if self.base_url.endswith("/embeddings") else self.base_url + "/embeddings"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(_RejectRedirects())

    def embed(self, texts: typing.Sequence[str]) -> typing.List[typing.List[float]]:
        if not texts:
            return []
        body = json.dumps({"model": self.model, "input": list(texts)}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(50 * 1024 * 1024 + 1)
        except EmbeddingError:
            raise
        except HTTPError as exc:
            raise EmbeddingError("Embedding 服务请求失败（HTTP %s）" % exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EmbeddingError("Embedding 服务不可达或请求超时") from exc
        if len(raw) > 50 * 1024 * 1024:
            raise EmbeddingError("Embedding 服务响应超过 50 MiB 限制")
        try:
            payload = json.loads(raw.decode("utf-8"))
            rows = sorted(payload["data"], key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmbeddingError("Embedding 服务返回格式不符合 OpenAI-compatible 协议") from exc
        if len(vectors) != len(texts) or not vectors or not vectors[0]:
            raise EmbeddingError("Embedding 服务返回的向量数量或维度不正确")
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions or any(not math.isfinite(value) for value in vector) for vector in vectors):
            raise EmbeddingError("Embedding 服务返回了非法向量")
        return vectors


def test_embedding_connection(base_url: str, model: str, api_key: typing.Optional[str]) -> int:
    return len(EmbeddingClient(base_url, model, api_key).embed(["OpenSLT embedding connection test"])[0])
