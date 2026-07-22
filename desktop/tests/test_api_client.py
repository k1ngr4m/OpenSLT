import httpx

from desktop.api_client import ApiClient
from desktop.models import ApiError


def test_login_and_refresh() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})
        if request.url.path.endswith("/auth/me") and len(calls) == 2:
            return httpx.Response(401, json={"code": "EXPIRED", "message": "过期"})
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(200, json={"access_token": "a2", "refresh_token": "r2"})
        return httpx.Response(200, json={"username": "admin"})

    api = ApiClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    assert api.login("admin", "password")["username"] == "admin"
    assert api.access_token == "a2"
    assert api.get("/auth/me")["username"] == "admin"
    assert "/api/v1/auth/refresh" in calls
    api.close()


def test_api_error_contains_trace_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, headers={"x-trace-id": "trace-1"}, json={"code": "BAD", "message": "参数错误"})

    api = ApiClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    try:
        api.get("/resources")
    except ApiError as exc:
        assert exc.code == "BAD"
        assert exc.trace_id == "trace-1"
    else:
        raise AssertionError("ApiError was not raised")
    api.close()
