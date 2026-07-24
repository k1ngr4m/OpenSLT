from __future__ import annotations

import typing
from contextlib import suppress
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncssh
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AuditLog
from app.services import order_configs
from app.services.order_configs import OrderConfigError, parse_xml, simulated_store


@pytest.fixture(autouse=True)
def reset_simulated_configs():
    simulated_store.clear()
    yield
    simulated_store.clear()


def create_order_resource(client: TestClient, headers: typing.Dict[str, str], name: str = "Order-Config") -> dict:
    response = client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "name": name,
            "resource_type": "order",
            "business_code": "fut_mm",
            "host": "127.0.0.1",
            "ssh_port": 22,
            "username": "tester",
            "auth_type": "password",
            "password": "secret",
            "remote_path": "/home/tester/ees_ef_vi_trader_binary_api_test",
            "capabilities": {"order_tool": "ees_ef_vi_trader_binary_api_test"},
            "version_info": "test",
            "notes": "",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_xml_parser_preserves_comments_and_rejects_unsafe_content():
    content = '''<?xml version="1.0" encoding="utf-8"?>
<tcp custom="值"><group id="one"><price disp="PRICE" value="1495" /></group><!-- keep --></tcp>'''
    declaration, document = parse_xml(content)
    assert declaration.startswith("<?xml")
    assert document["name"] == "tcp"
    assert document["attributes"] == [{"name": "custom", "value": "值"}]
    assert any(child["type"] == "comment" and child["text"] == " keep " for child in document["children"])

    with pytest.raises(OrderConfigError) as malformed:
        parse_xml("<tcp><broken></tcp>")
    assert malformed.value.code == "INVALID_ORDER_CONFIG_XML"
    with pytest.raises(OrderConfigError) as unsafe:
        parse_xml('<!DOCTYPE tcp [<!ENTITY x "secret">]><tcp>&x;</tcp>')
    assert unsafe.value.code == "UNSAFE_ORDER_CONFIG_XML"
    with pytest.raises(OrderConfigError) as encoding:
        parse_xml('<?xml version="1.0" encoding="gbk"?><tcp />')
    assert encoding.value.code == "ORDER_CONFIG_ENCODING"
    with pytest.raises(OrderConfigError) as oversized:
        parse_xml(f"<tcp>{'x' * (1024 * 1024)}</tcp>")
    assert oversized.value.code == "ORDER_CONFIG_TOO_LARGE"


def test_simulated_order_config_crud_conflict_and_audit(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_order_resource(client, admin_headers)
    base = f"/api/v1/resources/{resource['id']}/order-configs"

    listed = client.get(base, headers=admin_headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["simulated"] is True
    assert payload["tool"] == "ees_ef_vi_trader_binary_api_test"
    assert [item["name"] for item in payload["files"]] == ["ees_ef_vi_trader_api_test_conf.xml"]

    source_name = payload["files"][0]["name"]
    detail = client.get(f"{base}/{source_name}", headers=admin_headers).json()
    assert detail["document"]["name"] == "tcp"
    assert "PASSWORD" in detail["content"]

    new_name = "ees_ef_vi_trader_api_test_conf-order-tcp-1us-fut.xml"
    created = client.post(base, headers=admin_headers, json={"name": new_name, "source_name": source_name})
    assert created.status_code == 201
    created_detail = created.json()
    original_checksum = created_detail["checksum"]
    updated_content = created_detail["content"].replace('value="1495.0000"', 'value="1501.2500"')
    updated = client.put(
        f"{base}/{new_name}",
        headers=admin_headers,
        json={"content": updated_content, "expected_checksum": original_checksum},
    )
    assert updated.status_code == 200
    updated_detail = updated.json()
    assert updated_detail["checksum"] != original_checksum

    conflict = client.put(
        f"{base}/{new_name}",
        headers=admin_headers,
        json={"content": updated_content, "expected_checksum": original_checksum},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "ORDER_CONFIG_CHANGED"

    renamed_name = "ees_ef_vi_trader_api_test_conf-order-tcp-50us-fut.xml"
    renamed = client.patch(
        f"{base}/{new_name}",
        headers=admin_headers,
        json={"new_name": renamed_name, "expected_checksum": updated_detail["checksum"]},
    )
    assert renamed.status_code == 200
    renamed_detail = renamed.json()
    deleted = client.delete(
        f"{base}/{renamed_name}",
        headers=admin_headers,
        params={"expected_checksum": renamed_detail["checksum"]},
    )
    assert deleted.status_code == 204
    assert renamed_name not in [item["name"] for item in client.get(base, headers=admin_headers).json()["files"]]

    db = SessionLocal()
    try:
        audits = list(
            db.query(AuditLog)
            .filter(AuditLog.object_id == str(resource["id"]), AuditLog.action.like("order_config.%"))
            .all()
        )
        serialized = str([item.detail for item in audits])
        assert {item.action for item in audits} >= {
            "order_config.list",
            "order_config.read",
            "order_config.create",
            "order_config.update",
            "order_config.rename",
            "order_config.delete",
        }
        assert "PASSWORD" not in serialized
        assert "1501.2500" not in serialized
        assert "<tcp" not in serialized
    finally:
        db.close()


def test_order_config_security_and_role_boundary(client: TestClient, admin_headers: typing.Dict[str, str]):
    resource = create_order_resource(client, admin_headers, "Order-Security")
    base = f"/api/v1/resources/{resource['id']}/order-configs"
    traversal = client.get(f"{base}/not-the-right-prefix.xml", headers=admin_headers)
    assert traversal.status_code == 400
    assert traversal.json()["code"] == "INVALID_ORDER_CONFIG_NAME"

    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "config-viewer", "display_name": "配置访客", "password": "viewer-password", "role": "visitor"},
    )
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={"username": "config-viewer", "password": "viewer-password"})
    visitor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get(base, headers=visitor_headers).status_code == 403

    wrong_type = client.post(
        "/api/v1/resources",
        headers=admin_headers,
        json={
            "name": "REM-Not-Order",
            "resource_type": "rem",
            "business_code": "fut_mm",
            "host": "127.0.0.1",
            "username": "tester",
            "password": "secret",
            "remote_path": "/tmp/rem",
        },
    ).json()
    response = client.get(f"/api/v1/resources/{wrong_type['id']}/order-configs", headers=admin_headers)
    assert response.status_code == 400
    assert response.json()["code"] == "ORDER_RESOURCE_REQUIRED"


class FakeRemoteFile:
    def __init__(self, sftp: "FakeSFTP", path: str, mode: str) -> None:
        self.sftp = sftp
        self.path = path
        self.mode = mode

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def read(self, _: int) -> str:
        return self.sftp.files[self.path]["content"]

    async def write(self, content: str) -> int:
        self.sftp.files[self.path] = {
            "content": content,
            "permissions": 0o600,
            "mtime": int(datetime.now(timezone.utc).timestamp()),
            "type": asyncssh.FILEXFER_TYPE_REGULAR,
        }
        return len(content)


class FakeSFTP:
    def __init__(self, directory: str, filename: str, content: str) -> None:
        self.directory = directory
        self.files = {
            f"{directory}/{filename}": {
                "content": content,
                "permissions": 0o744,
                "mtime": 1_700_000_000,
                "type": asyncssh.FILEXFER_TYPE_REGULAR,
            },
            f"{directory}/ees_ef_vi_trader_api_test_conf-link.xml": {
                "content": content,
                "permissions": 0o777,
                "mtime": 1_700_000_000,
                "type": asyncssh.FILEXFER_TYPE_SYMLINK,
            },
        }
        self.renames: typing.List[typing.Tuple[str, str, str]] = []
        self.directories: typing.Set[str] = set()

    async def scandir(self, path: str):
        for full_path, item in list(self.files.items()):
            if full_path.rsplit("/", 1)[0] == path:
                attrs = asyncssh.SFTPAttrs(
                    type=item["type"],
                    size=len(item["content"].encode()),
                    permissions=item["permissions"],
                    mtime=item["mtime"],
                )
                yield SimpleNamespace(filename=full_path.rsplit("/", 1)[1], attrs=attrs)

    async def lstat(self, path: str):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("missing")
        item = self.files[path]
        return asyncssh.SFTPAttrs(
            type=item["type"],
            size=len(item["content"].encode()),
            permissions=item["permissions"],
            mtime=item["mtime"],
        )

    def open(self, path: str, mode: str, **_):
        return FakeRemoteFile(self, path, mode)

    async def exists(self, path: str) -> bool:
        return path in self.files

    async def setstat(self, path: str, attrs):
        self.files[path]["permissions"] = attrs.permissions

    async def posix_rename(self, old: str, new: str):
        self.renames.append(("replace", old, new))
        self.files[new] = self.files.pop(old)

    async def rename(self, old: str, new: str):
        self.renames.append(("rename", old, new))
        self.files[new] = self.files.pop(old)

    async def remove(self, path: str):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("missing")
        del self.files[path]

    async def makedirs(self, path: str, exist_ok: bool = False):
        self.directories.add(path)

    def exit(self):
        return None

    async def wait_closed(self):
        return None


class FakeConnection:
    def __init__(self, sftp: FakeSFTP) -> None:
        self.sftp = sftp

    async def start_sftp_client(self):
        return self.sftp

    def close(self):
        return None

    async def wait_closed(self):
        return None


def test_remote_sftp_atomic_update_permissions_and_trash(
    client: TestClient,
    admin_headers: typing.Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    resource = create_order_resource(client, admin_headers, "Order-Remote")
    directory = resource["remote_path"]
    filename = "ees_ef_vi_trader_api_test_conf.xml"
    content = '<?xml version="1.0" encoding="utf-8"?><tcp><price value="1495" /></tcp>'
    sftp = FakeSFTP(directory, filename, content)
    connection_options: typing.List[dict] = []

    async def fake_connect(**options):
        connection_options.append(options)
        return FakeConnection(sftp)

    monkeypatch.setattr(settings, "execution_mode", "remote")
    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    base = f"/api/v1/resources/{resource['id']}/order-configs"

    listed = client.get(base, headers=admin_headers).json()
    assert [row["name"] for row in listed["files"]] == [filename]
    detail = client.get(f"{base}/{filename}", headers=admin_headers).json()
    new_content = detail["content"].replace("1495", "1500")
    updated = client.put(
        f"{base}/{filename}",
        headers=admin_headers,
        json={"content": new_content, "expected_checksum": detail["checksum"]},
    )
    assert updated.status_code == 200
    assert sftp.files[f"{directory}/{filename}"]["permissions"] == 0o744
    assert any(operation == "replace" for operation, _, _ in sftp.renames)

    updated_detail = updated.json()
    deleted = client.delete(
        f"{base}/{filename}",
        headers=admin_headers,
        params={"expected_checksum": updated_detail["checksum"]},
    )
    assert deleted.status_code == 204
    assert f"{directory}/.openslt-config-trash" in sftp.directories
    assert any(".openslt-config-trash" in target for _, _, target in sftp.renames)
    assert connection_options
    assert connection_options[0]["username"] == "tester"
    assert connection_options[0]["password"] == "secret"

    with suppress(KeyError):
        del sftp.files[f"{directory}/{filename}"]
