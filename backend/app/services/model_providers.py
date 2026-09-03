from __future__ import annotations

import json
import typing
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActiveAiModel, AiModel, ModelProvider


MODEL_KINDS = frozenset({"chat", "embedding"})


class ModelProviderError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ModelProviderError("模型服务返回了重定向，已拒绝向其他地址发送凭据")


def normalize_provider_base_url(value: str, allow_insecure_http: bool) -> str:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("API Base URL 必须是完整的 HTTP 或 HTTPS URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("API Base URL 不得包含凭据、查询参数或片段")
    if parts.scheme == "http" and not allow_insecure_http:
        raise ValueError("HTTP 会明文传输 API Key 和业务资料，必须由管理员显式允许")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def list_provider_models(
    base_url: str,
    api_key: typing.Optional[str],
    timeout_seconds: int = 60,
) -> typing.List[str]:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/models"):
        endpoint += "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        with build_opener(_RejectRedirects()).open(
            Request(endpoint, headers=headers, method="GET"), timeout=timeout_seconds
        ) as response:
            raw = response.read(10 * 1024 * 1024 + 1)
    except ModelProviderError:
        raise
    except HTTPError as exc:
        raise ModelProviderError("获取模型列表失败（HTTP %s）" % exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ModelProviderError("模型服务不可达或请求超时") from exc
    if len(raw) > 10 * 1024 * 1024:
        raise ModelProviderError("模型列表响应超过 10 MiB 限制")
    try:
        rows = json.loads(raw.decode("utf-8"))["data"]
        model_ids = sorted({str(item["id"]).strip() for item in rows if str(item["id"]).strip()})
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ModelProviderError("模型列表格式不符合 OpenAI-compatible 协议") from exc
    return model_ids


def active_model(
    db: Session,
    kind: str,
) -> typing.Optional[typing.Tuple[ModelProvider, AiModel]]:
    row = db.execute(
        select(ModelProvider, AiModel)
        .join(AiModel, AiModel.provider_id == ModelProvider.id)
        .join(ActiveAiModel, ActiveAiModel.model_id == AiModel.id)
        .where(ActiveAiModel.kind == kind, AiModel.kind == kind)
    ).first()
    return (row[0], row[1]) if row else None


def require_active_model(db: Session, kind: str) -> typing.Tuple[ModelProvider, AiModel]:
    result = active_model(db, kind)
    if result is None:
        label = "对话" if kind == "chat" else "Embedding"
        raise ModelProviderError("尚未配置当前%s模型" % label)
    return result
