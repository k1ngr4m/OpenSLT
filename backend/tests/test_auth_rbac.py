from fastapi.testclient import TestClient

from conftest import create_resource


def test_login_refresh_and_role_boundary(client: TestClient, admin_headers: dict[str, str]):
    created = client.post("/api/v1/users", headers=admin_headers, json={"username": "viewer", "display_name": "访客", "password": "viewer-password", "role": "visitor"})
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "viewer-password"})
    assert login.status_code == 200
    tokens = login.json()
    viewer_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert client.get("/api/v1/resources", headers=viewer_headers).status_code == 200
    assert client.post("/api/v1/plans", headers=viewer_headers, json={}).status_code == 403
    assert client.get("/api/v1/users", headers=viewer_headers).status_code == 403
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_errors_include_trace_id(client: TestClient):
    response = client.get("/api/v1/resources")
    assert response.status_code == 401
    assert response.json()["trace_id"]
    assert response.headers["x-trace-id"] == response.json()["trace_id"]


def test_slnic_resource_type_is_supported(client: TestClient, admin_headers: dict[str, str]):
    resource = create_resource(client, admin_headers, "SLNIC-01", resource_type="slnic")
    assert resource["resource_type"] == "slnic"
