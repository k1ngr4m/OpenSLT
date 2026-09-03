from __future__ import annotations

import json
import typing
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class LlmError(RuntimeError):
    pass


def normalize_llm_base_url(value: str, allow_insecure_http: bool) -> str:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("生成模型 Base URL 必须是完整的 HTTP 或 HTTPS URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("生成模型 Base URL 不得包含凭据、查询参数或片段")
    if parts.scheme == "http" and not allow_insecure_http:
        raise ValueError("生成模型 HTTP 会明文传输 API Key 和需求资料，必须由管理员显式允许")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise LlmError("生成模型服务返回了重定向，已拒绝向其他地址发送资料")


class LlmClient:
    def __init__(self, base_url: str, model: str, api_key: typing.Optional[str], timeout_seconds: int = 180) -> None:
        self.endpoint = base_url.rstrip("/")
        if not self.endpoint.endswith("/chat/completions"):
            self.endpoint += "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(_RejectRedirects())

    def complete(self, messages: typing.Sequence[typing.Dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": list(messages), "temperature": 0.2}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(10 * 1024 * 1024 + 1)
        except LlmError:
            raise
        except HTTPError as exc:
            raise LlmError("生成模型请求失败（HTTP %s）" % exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LlmError("生成模型服务不可达或请求超时") from exc
        if len(raw) > 10 * 1024 * 1024:
            raise LlmError("生成模型响应超过 10 MiB 限制")
        try:
            return str(json.loads(raw.decode("utf-8"))["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LlmError("生成模型返回格式不符合 OpenAI-compatible 协议") from exc


def parse_cases(raw: str) -> typing.List[typing.Dict[str, typing.Any]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
        rows = value.get("cases") if isinstance(value, dict) else value
    except json.JSONDecodeError as exc:
        raise LlmError("生成模型未返回有效 JSON 用例") from exc
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise LlmError("生成模型返回的用例数量必须为 1 到 100 条")
    result = []
    titles = set()
    for row in rows:
        if not isinstance(row, dict):
            raise LlmError("生成模型返回了无效用例")
        title = str(row.get("title", "")).strip()
        steps = row.get("steps")
        expected = row.get("expected_results")
        if not title or not isinstance(steps, list) or not isinstance(expected, list) or not steps or len(steps) != len(expected) or any(not str(item).strip() for item in steps + expected):
            raise LlmError("每条用例必须包含名称及数量一致的测试步骤和预期结果")
        if title.casefold() in titles:
            raise LlmError("生成模型返回了重复的用例名称")
        titles.add(title.casefold())
        priority = str(row.get("priority") or "中").strip()
        if priority not in {"最高", "高", "中", "低"}:
            priority = "中"
        result.append({
            "title": title[:255],
            "preconditions": [str(item).strip() for item in row.get("preconditions", []) if str(item).strip()] if isinstance(row.get("preconditions", []), list) else [str(row.get("preconditions", "")).strip()],
            "steps": [str(item).strip() for item in steps],
            "expected_results": [str(item).strip() for item in expected],
            "case_type": str(row.get("case_type") or "功能")[:32],
            "priority": priority,
        })
    return result


def generate_cases(client: LlmClient, requirement: typing.Mapping[str, str], references: typing.Sequence[typing.Mapping[str, str]]) -> typing.List[typing.Dict[str, typing.Any]]:
    context = "\n\n".join("来源：%s（r%s）\n%s" % (item["source_path"], item["revision"], item["content"]) for item in references)
    prompt = (
        "需求编号：%s\n需求名称：%s\n需求来源：%s（r%s）\n\n参考资料：\n%s\n\n"
        "请生成可由人工执行的系统测试用例草稿。只返回 JSON：{\"cases\":[{\"title\":\"\",\"preconditions\":[],\"steps\":[],\"expected_results\":[],\"case_type\":\"功能\",\"priority\":\"中\"}]}。"
        "步骤与预期结果必须一一对应，覆盖正常、异常和边界场景，不要虚构资料中不存在的具体值。"
    ) % (requirement.get("requirement_no") or "未识别", requirement["requirement_name"], requirement["source_path"], requirement["revision"], context)
    return parse_cases(client.complete([
        {"role": "system", "content": "你是资深系统测试工程师，输出严格 JSON，不输出 Markdown。"},
        {"role": "user", "content": prompt},
    ]))


def test_llm_connection(base_url: str, model: str, api_key: typing.Optional[str]) -> None:
    content = LlmClient(base_url, model, api_key, timeout_seconds=60).complete([
        {"role": "user", "content": "仅回复 OK，用于 OpenSLT 连接测试。"},
    ])
    if not content.strip():
        raise LlmError("生成模型返回了空响应")
