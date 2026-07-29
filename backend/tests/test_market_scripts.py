from __future__ import annotations

import hashlib
from types import SimpleNamespace

import asyncssh

from app.core.database import SessionLocal
from app.models import Resource
from app.services import market_scripts
from conftest import create_resource


class FakeRemoteFile:
    def __init__(self, content: bytes):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self, _limit):
        return self.content


class FakeSFTP:
    def __init__(self):
        self.closed = False
        self.contents = {
            "start_b.sh": b"#!/bin/sh\necho b\n",
            "start_a.sh": b"#!/bin/sh\necho a\n",
            "disabled.sh": b"#!/bin/sh\necho disabled\n",
        }
        self.entries = [
            self.entry(name, asyncssh.FILEXFER_TYPE_REGULAR, 0o755, len(content))
            for name, content in self.contents.items()
        ] + [
            self.entry("readme.txt", asyncssh.FILEXFER_TYPE_REGULAR, 0o755, 4),
            self.entry("nested.sh", asyncssh.FILEXFER_TYPE_DIRECTORY, 0o755, 0),
            self.entry("linked.sh", asyncssh.FILEXFER_TYPE_SYMLINK, 0o755, 0),
            self.entry("huge.sh", asyncssh.FILEXFER_TYPE_REGULAR, 0o755, market_scripts.MAX_MARKET_SCRIPT_BYTES + 1),
        ]
        self.entries[2].attrs.permissions = 0o644

    @staticmethod
    def entry(filename, file_type, permissions, size):
        return SimpleNamespace(
            filename=filename,
            attrs=SimpleNamespace(
                type=file_type,
                permissions=permissions,
                size=size,
                mtime=1,
            ),
        )

    async def scandir(self, directory):
        assert directory == "/tmp/openslt"
        for entry in self.entries:
            yield entry

    def open(self, path, mode):
        assert mode == "rb"
        return FakeRemoteFile(self.contents[path.rsplit("/", 1)[-1]])

    def exit(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.sftp = FakeSFTP()
        self.closed = False

    async def start_sftp_client(self):
        return self.sftp

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


def test_market_script_endpoint_lists_root_regular_shell_scripts(
    client, admin_headers, monkeypatch
):
    connections = []

    async def fake_connect(**options):
        assert options["password"] == "secret"
        connection = FakeConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(market_scripts.asyncssh, "connect", fake_connect)
    resource = create_resource(client, admin_headers, "Market-scripts", resource_type="market")
    response = client.get(
        f"/api/v1/resources/{resource['id']}/market-scripts", headers=admin_headers
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["directory"] == "/tmp/openslt"
    assert [item["name"] for item in payload["files"]] == [
        "disabled.sh",
        "start_a.sh",
        "start_b.sh",
    ]
    by_name = {item["name"]: item for item in payload["files"]}
    assert by_name["disabled.sh"]["executable"] is False
    assert by_name["start_a.sh"]["executable"] is True
    assert by_name["start_a.sh"]["checksum"] == hashlib.sha256(
        b"#!/bin/sh\necho a\n"
    ).hexdigest()
    assert connections[0].sftp.closed is True
    assert connections[0].closed is True


def test_market_script_endpoint_rejects_wrong_or_disabled_resource(client, admin_headers):
    rem = create_resource(client, admin_headers, "REM-not-market")
    wrong = client.get(f"/api/v1/resources/{rem['id']}/market-scripts", headers=admin_headers)
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "MARKET_RESOURCE_REQUIRED"

    market = create_resource(client, admin_headers, "Market-disabled", resource_type="market")
    with SessionLocal() as db:
        record = db.get(Resource, market["id"])
        record.is_enabled = False
        db.commit()
    disabled = client.get(
        f"/api/v1/resources/{market['id']}/market-scripts", headers=admin_headers
    )
    assert disabled.status_code == 409
    assert disabled.json()["code"] == "MARKET_RESOURCE_DISABLED"


def test_market_script_endpoint_reports_sftp_failure(client, admin_headers, monkeypatch):
    async def failed_connect(**_options):
        raise OSError("network unreachable")

    monkeypatch.setattr(market_scripts.asyncssh, "connect", failed_connect)
    market = create_resource(client, admin_headers, "Market-offline", resource_type="market")
    response = client.get(
        f"/api/v1/resources/{market['id']}/market-scripts", headers=admin_headers
    )
    assert response.status_code == 502
    assert response.json()["code"] == "MARKET_SCRIPT_SFTP_FAILED"
