from __future__ import annotations

import typing
import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./backend/data/test-openslt.sqlite3"
os.environ["ARTIFACT_ROOT"] = "./backend/data/test-artifacts"
os.environ["EXECUTION_MODE"] = "simulated"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-openslt"

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.main import app, seed_database


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_database()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers(client: TestClient) -> typing.Dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "shengli123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_resource(client: TestClient, headers: typing.Dict[str, str], name: str, resource_type: str = "rem") -> dict:
    response = client.post("/api/v1/resources", headers=headers, json={"name": name, "resource_type": resource_type, "business_code": "fut_mm", "host": "127.0.0.1", "ssh_port": 22, "username": "tester", "auth_type": "password", "password": "secret", "remote_path": "/tmp/openslt", "capabilities": {}, "version_info": "test", "notes": "", "is_enabled": True})
    assert response.status_code == 201, response.text
    return response.json()


def create_plan_scenario(client: TestClient, headers: typing.Dict[str, str], required_types: typing.Union[typing.List[str], None] = None) -> typing.Tuple[dict, dict]:
    plan_response = client.post("/api/v1/plans", headers=headers, json={"name": "基础测速方案", "business_code": "fut_mm", "description": "test", "default_resource_ids": [], "config_version": "1.0", "is_enabled": True})
    assert plan_response.status_code == 201
    plan = plan_response.json()
    scenario_response = client.post("/api/v1/scenarios", headers=headers, json={"plan_id": plan["id"], "name": "发单延迟", "scenario_type": "order", "config_version": "1.0", "expected_artifacts": ["pcapng"], "required_resource_types": required_types or ["rem"], "is_enabled": True})
    assert scenario_response.status_code == 201
    return plan, scenario_response.json()
