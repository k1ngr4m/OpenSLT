from __future__ import annotations
from pathlib import Path


def test_frontend_and_spa_routes_are_served(client):
    if not Path("frontend/dist/index.html").is_file():
        return
    index = client.get("/")
    assert index.status_code == 200
    assert "OpenSLT" in index.text
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.text == index.text


def test_api_routes_take_precedence_over_spa(client, admin_headers):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "openslt-api"
    me = client.get("/api/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

