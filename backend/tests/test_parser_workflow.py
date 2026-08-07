from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest

from app.models import Artifact
from app.core.database import SessionLocal
from app.models import Resource
from app.services import order_configs
from app.services import terminal as terminal_service
from app.services import orchestration, workflows
from app.workflow_node_configs import PARSER_ACTIONS
from conftest import create_plan_scenario, create_resource, publish_workflow


PARSER_TOOLS = [
    "soft_cffex_speed_analysis",
    "soft_cffex_speed_analysis_v2",
    "soft_shfe_speed_analysis_v2",
    "soft_czce_speed_analysis",
    "soft_dce_speed_analysis_v7",
    "soft_gfex_speed_analysis",
    "hwcffex_1414_2.0",
    "hwshfe_1414_2.0",
    "mg11",
]


class FakeRemoteFile:
    def __init__(self, sftp: "FakeConfigSFTP", path: str) -> None:
        self.sftp = sftp
        self.path = path

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
            "mtime": 1_700_000_100,
            "type": asyncssh.FILEXFER_TYPE_REGULAR,
        }
        return len(content)


class FakeConfigSFTP:
    def __init__(self) -> None:
        self.files = {}
        self.directories = set()

    async def makedirs(self, path: str, exist_ok: bool = False):
        self.directories.add(path)

    async def exists(self, path: str) -> bool:
        return path in self.files

    def open(self, path: str, mode: str, **_):
        return FakeRemoteFile(self, path)

    async def scandir(self, path: str):
        for full_path, item in list(self.files.items()):
            if full_path.rsplit("/", 1)[0] != path:
                continue
            yield SimpleNamespace(
                filename=full_path.rsplit("/", 1)[1],
                attrs=asyncssh.SFTPAttrs(
                    type=item["type"],
                    size=len(item["content"].encode()),
                    permissions=item["permissions"],
                    mtime=item["mtime"],
                ),
            )

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

    async def setstat(self, path: str, attrs):
        self.files[path]["permissions"] = attrs.permissions

    async def rename(self, old: str, new: str):
        self.files[new] = self.files.pop(old)

    async def posix_rename(self, old: str, new: str):
        self.files[new] = self.files.pop(old)

    async def remove(self, path: str):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("missing")
        del self.files[path]

    def exit(self):
        return None

    async def wait_closed(self):
        return None


class FakeConfigConnection:
    def __init__(self, sftp: FakeConfigSFTP) -> None:
        self.sftp = sftp

    async def start_sftp_client(self):
        return self.sftp

    def close(self):
        return None

    async def wait_closed(self):
        return None


def create_parser_resource(client, headers, tool="soft_cffex_speed_analysis_v2", business_code="fut_mm"):
    config = f"{tool[:-3] if tool.endswith('_v2') else tool}.xml"
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": f"Parser-{tool}", "resource_type": "parser", "business_code": business_code,
        "host": "127.0.0.1", "ssh_port": 22, "username": "tester",
        "auth_type": "password", "password": "secret", "remote_path": "",
        "capabilities": {
            "parser_tool": tool, "parser_binary": tool, "parser_config_filename": config,
        },
        "version_info": "test", "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_database_resource(client, headers, database_names=None):
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": "Parser-Database", "resource_type": "database", "business_code": "fut_mm",
        "host": "", "ssh_port": 22, "username": "", "auth_type": "password",
        "database_engine": "mysql", "database_connection_mode": "direct",
        "database_host": "127.0.0.1", "database_port": 3306,
        "database_names": database_names or ["fut_mm_config", "fut_mm_trading_data"],
        "database_username": "tester",
        "database_password": "secret", "database_tls_enabled": False,
        "remote_path": "", "capabilities": {}, "version_info": "test",
        "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def parser_nodes(parser_config=None):
    return [
        {"node_key": "start", "node_type": "slnic_start_capture", "name": "Start", "config": {}},
        {"node_key": "stop", "node_type": "slnic_stop_capture", "name": "Stop", "config": {}},
        {"node_key": "merge", "node_type": "slnic_merge_capture", "name": "Merge", "config": {}},
        {
            "node_key": "parse", "node_type": "parser_parse", "name": "Parse",
            "config": {"database_name": "fut_mm_trading_data", **(parser_config or {})},
        },
    ]


def parser_xml_config(client, headers, resource):
    base = f"/api/v1/resources/{resource['id']}/parser-configs"
    files = client.get(base, headers=headers)
    assert files.status_code == 200, files.text
    main = resource["capabilities"]["parser_config_filename"]
    details = {
        name: client.get(f"{base}/{name}", headers=headers).json()
        for name in ("config.xml", "instance.xml", main)
    }
    return {
        "config_xml_filename": "config.xml",
        "config_xml_checksum": details["config.xml"]["checksum"],
        "instance_xml_filename": "instance.xml",
        "instance_xml_checksum": details["instance.xml"]["checksum"],
        "analysis_xml_filename": main,
        "analysis_xml_checksum": details[main]["checksum"],
    }


def complete_workflow(client, headers, run_id):
    for _ in range(20):
        run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        if run["status"] == "completed":
            return run
        step = next(item for item in run["steps"] if item["status"] != "succeeded")
        operation = "complete" if step["status"] == "waiting" else "start"
        response = client.post(
            f"/api/v1/runs/{run_id}/steps/{step['id']}/{operation}", headers=headers
        )
        assert response.status_code == 200, response.text
    raise AssertionError("workflow did not complete")


def test_parser_tools_are_available_to_every_business(client, admin_headers):
    businesses = ["fut_mm", "rem_two", "rem_two_mm"]
    for index, tool in enumerate(PARSER_TOOLS):
        resource = create_parser_resource(
            client, admin_headers, tool=tool, business_code=businesses[index % len(businesses)]
        )
        expected_config = f"{tool[:-3] if tool.endswith('_v2') else tool}.xml"
        assert resource["remote_path"] == f"/home/user0/{tool}"
        assert resource["capabilities"]["parser_binary"] == tool
        assert resource["capabilities"]["parser_config_filename"] == expected_config
        assert resource["capabilities"]["parser_actions"] == list(PARSER_ACTIONS)


def test_parser_resource_actions_accept_explicit_subset_and_reject_unknown(client, admin_headers):
    resource = create_parser_resource(client, admin_headers)
    subset = [PARSER_ACTIONS[2], PARSER_ACTIONS[0], PARSER_ACTIONS[2]]
    updated = client.put(
        f"/api/v1/resources/{resource['id']}",
        headers=admin_headers,
        json={
            "name": resource["name"],
            "resource_type": "parser",
            "business_code": resource["business_code"],
            "host": resource["host"],
            "ssh_port": resource["ssh_port"],
            "username": resource["username"],
            "auth_type": "password",
            "remote_path": resource["remote_path"],
            "capabilities": {
                **resource["capabilities"],
                "parser_actions": subset,
            },
            "version_info": resource["version_info"],
            "notes": resource["notes"],
            "is_enabled": True,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["capabilities"]["parser_actions"] == [PARSER_ACTIONS[2], PARSER_ACTIONS[0]]

    explicit_empty = client.put(
        f"/api/v1/resources/{resource['id']}",
        headers=admin_headers,
        json={
            "name": resource["name"],
            "resource_type": "parser",
            "business_code": resource["business_code"],
            "host": resource["host"],
            "ssh_port": resource["ssh_port"],
            "username": resource["username"],
            "auth_type": "password",
            "remote_path": resource["remote_path"],
            "capabilities": {**resource["capabilities"], "parser_actions": []},
            "version_info": resource["version_info"],
            "notes": resource["notes"],
            "is_enabled": True,
        },
    )
    assert explicit_empty.status_code == 200, explicit_empty.text
    assert explicit_empty.json()["capabilities"]["parser_actions"] == []

    invalid = client.put(
        f"/api/v1/resources/{resource['id']}",
        headers=admin_headers,
        json={
            "name": resource["name"],
            "resource_type": "parser",
            "business_code": resource["business_code"],
            "host": resource["host"],
            "ssh_port": resource["ssh_port"],
            "username": resource["username"],
            "auth_type": "password",
            "remote_path": resource["remote_path"],
            "capabilities": {**resource["capabilities"], "parser_actions": ["rm -rf /"]},
            "version_info": resource["version_info"],
            "notes": resource["notes"],
            "is_enabled": True,
        },
    )
    assert invalid.status_code == 422


def test_existing_parser_resource_without_action_config_reads_all_actions(client, admin_headers):
    resource = create_parser_resource(client, admin_headers)
    with SessionLocal() as db:
        stored = db.get(Resource, resource["id"])
        stored.capabilities = {
            key: value for key, value in stored.capabilities.items() if key != "parser_actions"
        }
        db.commit()

    listed = client.get("/api/v1/resources", headers=admin_headers)

    assert listed.status_code == 200, listed.text
    loaded = next(item for item in listed.json() if item["id"] == resource["id"])
    assert loaded["capabilities"]["parser_actions"] == list(PARSER_ACTIONS)


def test_parser_config_defaults_and_crud(client, admin_headers, monkeypatch):
    resource = create_parser_resource(client, admin_headers)
    sftp = FakeConfigSFTP()

    async def fake_connect(**_options):
        return FakeConfigConnection(sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    base = f"/api/v1/resources/{resource['id']}/parser-configs"
    listed = client.get(base, headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert {item["name"] for item in listed.json()["files"]} == {
        "config.xml", "instance.xml", "soft_cffex_speed_analysis.xml",
    }
    config = client.get(f"{base}/config.xml", headers=admin_headers).json()
    assert 'name="100001"' in config["content"]
    instance = client.get(f"{base}/instance.xml", headers=admin_headers).json()
    assert 'user_id="222201"' in instance["content"]
    created = client.post(base, headers=admin_headers, json={
        "name": "scenario.xml", "source_name": "soft_cffex_speed_analysis.xml",
    })
    assert created.status_code == 201, created.text
    detail = created.json()
    updated = client.put(f"{base}/scenario.xml", headers=admin_headers, json={
        "content": detail["content"].replace('market_ip value=""', 'market_ip value="10.0.0.2"'),
        "expected_checksum": detail["checksum"],
    })
    assert updated.status_code == 200, updated.text
    renamed = client.patch(f"{base}/scenario.xml", headers=admin_headers, json={
        "new_name": "scenario-renamed.xml", "expected_checksum": updated.json()["checksum"],
    })
    assert renamed.status_code == 200, renamed.text
    deleted = client.delete(
        f"{base}/scenario-renamed.xml", headers=admin_headers,
        params={"expected_checksum": renamed.json()["checksum"]},
    )
    assert deleted.status_code == 204


def test_soft_cffex_speed_analysis_uses_tcp_default_config(client, admin_headers, monkeypatch):
    resource = create_parser_resource(
        client, admin_headers, tool="soft_cffex_speed_analysis"
    )
    sftp = FakeConfigSFTP()

    async def fake_connect(**_options):
        return FakeConfigConnection(sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    base = f"/api/v1/resources/{resource['id']}/parser-configs"
    detail = client.get(f"{base}/soft_cffex_speed_analysis.xml", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    content = detail.json()["content"]
    assert "<tcp>" in content
    assert '<group_quote_src_conf id="quote_src_conf">' in content
    assert '<quote_file_name value="merge_pcap.pcapng"/>' in content
    assert '<rem_client_ip value="180.1.1.31"/>' in content
    assert '<rem_ip value="10.1.51.107"/>' in content
    assert '<market_ip value="10.1.51.8"/>' in content
    assert '<enable_log_disp disp="disable:0, enable:1" default_value="" value="1"/>' in content
    assert '<account_exchange_code_file_name value="t_account_exchange_code.csv"/>' in content
    assert '<fut_orders_file_name value="t_fut_orders.csv"/>' in content
    assert '<fut_quotes_file_name value="t_fut_quotes.csv"/>' in content


def test_soft_cffex_speed_analysis_v2_uses_tcp_default_config(
    client, admin_headers, monkeypatch
):
    resource = create_parser_resource(
        client, admin_headers, tool="soft_cffex_speed_analysis_v2"
    )
    sftp = FakeConfigSFTP()

    async def fake_connect(**_options):
        return FakeConfigConnection(sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    base = f"/api/v1/resources/{resource['id']}/parser-configs"
    detail = client.get(f"{base}/soft_cffex_speed_analysis.xml", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    content = detail.json()["content"]
    assert "<tcp>" in content
    assert '<group_quote_src_conf id="quote_src_conf">' in content
    assert '<quote_file_name value="merge_pcap.pcapng"/>' in content
    assert '<rem_client_ip value="180.1.1.181"/>' in content
    assert '<rem_ip value="10.1.51.107"/>' in content
    assert '<market_ip value="10.1.51.129"/>' in content
    assert '<parse_type value="soft" disp="soft:软核  mg11:mg版本"/>' in content
    assert '<enable_log_disp disp="disable:0, enable:1" default_value="" value="1"/>' in content
    assert '<account_exchange_code_file_name value="t_account_exchange_code.csv"/>' in content
    assert '<fut_orders_file_name value="t_fut_orders.csv"/>' in content
    assert '<fut_quotes_file_name value="t_fut_quotes.csv"/>' in content
    assert '<sm4_key value="30313233343536373839303132333435"/>' in content
    assert '<sm4_iv value="39383736353433323130393837363534"/>' in content


def test_parser_publish_does_not_require_merge_node_position(client, admin_headers):
    database = create_database_resource(client, admin_headers)
    parser = create_parser_resource(client, admin_headers)
    _, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[database["id"], parser["id"]]
    )
    document = client.get(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers).json()
    saved = client.put(f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers, json={
        "expected_revision": document["draft"]["revision"],
        "resource_ids": [database["id"], parser["id"]],
        "nodes": [parser_nodes()[-1]],
    })
    assert saved.status_code == 200, saved.text
    published = client.post(f"/api/v1/scenarios/{scenario['id']}/workflow/publish", headers=admin_headers)
    assert published.status_code == 422
    assert not any("pcapng" in item["message"] for item in published.json()["errors"])


def test_parser_publish_rejects_missing_paired_config_database(client, admin_headers):
    database = create_database_resource(
        client,
        admin_headers,
        database_names=["fut_mm_trading_data"],
    )
    parser = create_parser_resource(client, admin_headers)
    _, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[database["id"], parser["id"]],
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [database["id"], parser["id"]],
            "nodes": [parser_nodes()[-1]],
        },
    )
    assert saved.status_code == 200, saved.text

    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish",
        headers=admin_headers,
    )

    assert published.status_code == 422
    assert any(
        item.get("field") == "database_name"
        and "fut_mm_config" in item["message"]
        for item in published.json()["errors"]
    )


def test_parser_publish_rejects_changed_selected_xml(client, admin_headers, monkeypatch):
    config_sftp = FakeConfigSFTP()

    async def fake_connect(**_options):
        return FakeConfigConnection(config_sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_connect)
    slnic = create_resource(client, admin_headers, "SLNIC-Parser-Config", resource_type="slnic")
    database = create_database_resource(client, admin_headers)
    parser = create_parser_resource(client, admin_headers)
    xml_config = parser_xml_config(client, admin_headers, parser)
    _, scenario = create_plan_scenario(
        client,
        admin_headers,
        resource_ids=[slnic["id"], database["id"], parser["id"]],
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [slnic["id"], database["id"], parser["id"]],
            "nodes": parser_nodes(xml_config),
        },
    )
    assert saved.status_code == 200, saved.text

    base = f"/api/v1/resources/{parser['id']}/parser-configs/config.xml"
    detail = client.get(base, headers=admin_headers).json()
    changed = client.put(
        base,
        headers=admin_headers,
        json={
            "content": detail["content"].replace("100001", "100002"),
            "expected_checksum": detail["checksum"],
        },
    )
    assert changed.status_code == 200, changed.text
    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish",
        headers=admin_headers,
    )
    assert published.status_code == 422
    assert any("发生变化" in item["message"] for item in published.json()["errors"])


@pytest.mark.asyncio
async def test_parser_runtime_xml_fallback_and_checksum_validation(monkeypatch):
    resource = SimpleNamespace(
        capabilities={"parser_config_filename": "soft_cffex_speed_analysis.xml"},
    )
    requested = []

    async def fake_read(_resource, filename):
        requested.append(filename)
        return {"name": filename, "content": "<root />", "checksum": "a" * 64}

    monkeypatch.setattr(order_configs.order_config_service, "read", fake_read)
    loaded = await workflows._load_parser_xml_files(resource, {})
    assert requested == ["config.xml", "instance.xml", "soft_cffex_speed_analysis.xml"]
    assert set(loaded) == {"config", "instance", "analysis"}

    with pytest.raises(workflows.WorkflowError) as changed:
        await workflows._load_parser_xml_files(resource, {
            "config_xml_filename": "config.xml",
            "config_xml_checksum": "b" * 64,
            "instance_xml_filename": "instance.xml",
            "instance_xml_checksum": "a" * 64,
            "analysis_xml_filename": "soft_cffex_speed_analysis.xml",
            "analysis_xml_checksum": "a" * 64,
        })
    assert changed.value.code == "PARSER_CONFIG_CHANGED"


@pytest.mark.asyncio
async def test_parser_table_export_allows_empty_table(monkeypatch, tmp_path):
    class FakeCursor:
        description = (("id",), ("latency_ns",))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement):
            assert statement == "SELECT * FROM `t_fut_orders`"

        def fetchmany(self, _size):
            return []

    class FakeConnection:
        def cursor(self, _cursor_type):
            return FakeCursor()

    @asynccontextmanager
    async def fake_connection(_resource, database_name):
        assert database_name == "fut_mm_trading_data"
        yield FakeConnection()

    monkeypatch.setattr(workflows.mysql_adapter, "connection", fake_connection)
    target = tmp_path / "t_fut_orders.csv"

    row_count = await workflows._export_parser_table(
        SimpleNamespace(),
        "fut_mm_trading_data",
        "t_fut_orders",
        target,
    )

    assert row_count == 0
    assert target.read_text(encoding="utf-8") == "id,latency_ns\n"


def test_remote_parser_uploads_inputs_executes_and_downloads_changed_csv(
    client, admin_headers, monkeypatch
):
    config_sftp = FakeConfigSFTP()

    async def fake_config_connect(**_options):
        return FakeConfigConnection(config_sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_config_connect)
    slnic = create_resource(client, admin_headers, "SLNIC-Remote-Parser", resource_type="slnic")
    with SessionLocal() as db:
        db.get(Resource, slnic["id"]).remote_path = "/home/user0/slnic"
        db.commit()
    database = create_database_resource(client, admin_headers)
    parser = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[slnic["id"], database["id"], parser["id"]]
    )
    xml_config = parser_xml_config(client, admin_headers, parser)
    publish_workflow(
        client, admin_headers, scenario,
        [slnic["id"], database["id"], parser["id"]], parser_nodes(xml_config),
    )

    async def fake_slnic(db, run, step, node, run_resources):
        if node.node_type != "slnic_merge_capture":
            return {"exit_code": 0}
        target = workflows._slnic_artifact_path(run, step)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"remote-pcapng")
        artifact = Artifact(
            run_id=run.id, step_id=step.id, artifact_type="packet_capture",
            name="merge_pcap.pcapng", path=str(target), content_type="application/vnd.tcpdump.pcap",
            size=target.stat().st_size, checksum="pcap-checksum", is_immutable=True,
        )
        db.add(artifact)
        db.flush()
        return {"exit_code": 0, "artifact_id": artifact.id}

    export_calls = []

    async def fake_export(database_resource, database_name, table, target):
        export_calls.append((database_name, table))
        if table == "t_fut_orders":
            target.write_text("id,account\n", encoding="utf-8")
            return 0
        target.write_text("id,account\n1,100001\n", encoding="utf-8")
        return 1

    class FakeSFTP:
        def __init__(self):
            self.files = {
                "/home/user0/soft_cffex_speed_analysis_v2/existing.csv": b"old",
                "/home/user0/soft_cffex_speed_analysis_v2/analysis-result.csv": b"old-result",
            }
            self.mtimes = {name: 1 for name in self.files}
            self.closed = False

        async def makedirs(self, path, exist_ok=False):
            return None

        def scandir(self, directory):
            async def entries():
                prefix = directory.rstrip("/") + "/"
                for path, content in sorted(self.files.items()):
                    if path.startswith(prefix) and "/" not in path[len(prefix):]:
                        yield SimpleNamespace(
                            filename=path[len(prefix):],
                            attrs=SimpleNamespace(
                                type=workflows.asyncssh.FILEXFER_TYPE_REGULAR,
                                size=len(content), mtime=self.mtimes[path],
                            ),
                        )
            return entries()

        async def put(self, local_path, remote_path):
            self.files[remote_path] = Path(local_path).read_bytes()
            self.mtimes[remote_path] = 2

        async def posix_rename(self, source, target):
            self.files[target] = self.files.pop(source)
            self.mtimes[target] = self.mtimes.pop(source)

        async def remove(self, path):
            self.files.pop(path, None)
            self.mtimes.pop(path, None)

        async def get(self, remote_path, local_path):
            Path(local_path).write_bytes(self.files[remote_path])

        def exit(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.sftp = FakeSFTP()
            self.process = None
            self.writes = []
            self.remote_workdir = ""
            self.closed = False

        async def start_sftp_client(self):
            return self.sftp

        async def create_process(self, _command, **_options):
            connection = self

            class Stdin:
                def write(self, data):
                    connection.writes.append(data)
                    if data == "\x03":
                        connection.process.stdout.stop.set()
                    if "soft_cffex_speed_analysis.xml" in data:
                        connection.remote_workdir = data.split(" && ", 1)[0].replace("cd ", "", 1)
                    if data == f"{PARSER_ACTIONS[0]}\r":
                        output_path = f"{connection.remote_workdir}/analysis-result.csv"
                        connection.sftp.files[output_path] = b"sequence,latency_us\n1,82.1\n"
                        connection.sftp.mtimes[output_path] = 3

            class Stdout:
                def __init__(self):
                    self.ready = True
                    self.stop = asyncio.Event()

                async def read(self, _size):
                    if self.ready:
                        self.ready = False
                        return "parser-shell-ready\r\n"
                    await self.stop.wait()
                    return ""

            class Process:
                stdin = Stdin()
                stdout = Stdout()
                exit_status = 0

                def change_terminal_size(self, _columns, _rows):
                    return None

                def close(self):
                    return None

                async def wait(self):
                    return None

                async def wait_closed(self):
                    return None

            self.process = Process()
            return self.process

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    connection = FakeConnection()

    async def fake_connect(**kwargs):
        return connection

    monkeypatch.setattr(workflows, "execute_slnic_node", fake_slnic)
    monkeypatch.setattr(workflows, "_export_parser_table", fake_export)
    async def fake_load_xml(_resource, _raw_config):
        return {
            "config": {"name": "config.xml", "content": "<root />", "checksum": xml_config["config_xml_checksum"]},
            "instance": {"name": "instance.xml", "content": "<root />", "checksum": xml_config["instance_xml_checksum"]},
            "analysis": {"name": "soft_cffex_speed_analysis.xml", "content": "<tcp />", "checksum": xml_config["analysis_xml_checksum"]},
        }

    monkeypatch.setattr(workflows, "_load_parser_xml_files", fake_load_xml)
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(terminal_service.asyncssh, "connect", fake_connect)

    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"],
        "resource_ids": [slnic["id"], database["id"], parser["id"]],
        "timeout_minutes": 30,
    }).json()
    client.post(f"/api/v1/runs/{created['id']}/start", headers=admin_headers)

    for _ in range(10):
        pending_run = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
        current = next(item for item in pending_run["steps"] if item["status"] != "succeeded")
        if current["node_type"] == "parser_parse":
            break
        operation = "complete" if current["status"] == "waiting" else "start"
        response = client.post(
            f"/api/v1/runs/{created['id']}/steps/{current['id']}/{operation}",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
    else:
        raise AssertionError("parser step was not reached")

    export_url = f"/api/v1/runs/{created['id']}/steps/{current['id']}/parser-exports"
    first_export = client.post(
        export_url,
        headers=admin_headers,
        json={"table": "t_fut_orders"},
    )
    assert first_export.status_code == 200, first_export.text
    refreshed_export = client.post(
        export_url,
        headers=admin_headers,
        json={"table": "t_fut_orders"},
    )
    assert refreshed_export.status_code == 200, refreshed_export.text
    assert refreshed_export.json()["artifact_id"] == first_export.json()["artifact_id"]
    assert refreshed_export.json()["row_count"] == 0
    account_export = client.post(
        export_url,
        headers=admin_headers,
        json={"table": "t_account_exchange_code"},
    )
    assert account_export.status_code == 200, account_export.text
    assert account_export.json()["database_name"] == "fut_mm_config"
    invalid_export = client.post(
        export_url,
        headers=admin_headers,
        json={"table": "t_users"},
    )
    assert invalid_export.status_code == 422

    ordinary_start = client.post(
        f"/api/v1/runs/{created['id']}/steps/{current['id']}/start",
        headers=admin_headers,
    )
    assert ordinary_start.status_code == 409
    assert ordinary_start.json()["code"] == "PARSER_TERMINAL_REQUIRED"

    token = admin_headers["Authorization"][len("Bearer ") :]
    terminal_path = f"/api/v1/ws/resources/{parser['id']}/terminal?token={token}"
    with client.websocket_connect(terminal_path) as websocket:
        assert websocket.receive_json()["status"] == "connecting"
        assert websocket.receive_json()["status"] == "connected"
        assert "parser-shell-ready" in websocket.receive_json()["data"]
        websocket.send_json({
            "type": "workflow_step_command",
            "run_id": created["id"],
            "step_id": current["id"],
            "operation": "start",
        })
        dispatched = websocket.receive_json()
        assert dispatched["status"] == "dispatched"
        assert dispatched["supported_parser_actions"] == list(PARSER_ACTIONS)
        missing_output = client.post(
            f"/api/v1/runs/{created['id']}/steps/{current['id']}/complete",
            headers=admin_headers,
        )
        assert missing_output.status_code == 409
        assert missing_output.json()["code"] == "PARSER_OUTPUT_MISSING"
        still_waiting = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()
        assert still_waiting["status"] == "awaiting_step_completion"
        assert still_waiting["steps"][-1]["status"] == "waiting"
        websocket.send_json({
            "type": "parser_action",
            "run_id": created["id"],
            "step_id": current["id"],
            "action": PARSER_ACTIONS[0],
        })
        action_response = websocket.receive_json()
        assert action_response["status"] == "dispatched"
        completed = client.post(
            f"/api/v1/runs/{created['id']}/steps/{current['id']}/complete",
            headers=admin_headers,
        )
        assert completed.status_code == 200, completed.text
        websocket.send_json({"type": "input", "data": "\x03"})
        assert websocket.receive_json()["type"] == "exit"

    run = client.get(f"/api/v1/runs/{created['id']}", headers=admin_headers).json()

    assert run["status"] == "completed"
    parsed = [item for item in run["artifacts"] if item["artifact_type"] == "parsed_csv"]
    assert [item["name"] for item in parsed] == ["analysis-result.csv"]
    parser_command = next(item.rstrip("\r") for item in connection.writes if "soft_cffex_speed_analysis.xml" in item)
    assert parser_command == (
        "cd /home/user0/soft_cffex_speed_analysis_v2 && "
        "/home/user0/soft_cffex_speed_analysis_v2/soft_cffex_speed_analysis_v2 "
        "soft_cffex_speed_analysis.xml"
    )
    assert f"{PARSER_ACTIONS[0]}\r" in connection.writes
    assert connection.writes[-1] == "\x03"
    remote_workdir = parser_command.split(" && ", 1)[0].replace("cd ", "", 1)
    for filename in (
        "t_fut_orders.csv",
        "t_fut_quotes.csv",
        "t_fut_arbi_orders.csv",
        "t_account_exchange_code.csv",
    ):
        assert f"{remote_workdir}/{filename}" in connection.sftp.files
    assert connection.closed is True
    assert connection.sftp.closed is True
    assert export_calls == [
        ("fut_mm_trading_data", "t_fut_orders"),
        ("fut_mm_trading_data", "t_fut_orders"),
        ("fut_mm_config", "t_account_exchange_code"),
        ("fut_mm_trading_data", "t_fut_quotes"),
        ("fut_mm_trading_data", "t_fut_arbi_orders"),
    ]
    input_artifacts = [item for item in run["artifacts"] if item["artifact_type"] == "parser_input_csv"]
    assert {item["name"] for item in input_artifacts} == {
        "t_fut_orders.csv", "t_fut_quotes.csv", "t_fut_arbi_orders.csv",
        "t_account_exchange_code.csv",
    }
    parse_step = next(item for item in run["steps"] if item["node_type"] == "parser_parse")
    assert parse_step["result_summary"]["config_database_name"] == "fut_mm_config"
    assert parse_step["result_summary"]["mode"] == "terminal"
    assert parse_step["result_summary"]["remote_workdir"] == "/home/user0/soft_cffex_speed_analysis_v2"
    assert set(parse_step["result_summary"]["remote_csv_snapshot"]) == {
        "analysis-result.csv",
        "existing.csv",
    }
    assert parse_step["result_summary"]["parser_action_history"][-1]["action"] == PARSER_ACTIONS[0]
    exports = parse_step["result_summary"]["parser_input_exports"]
    assert exports["t_fut_orders"]["source"] == "manual"
    assert exports["t_fut_orders"]["row_count"] == 0
    assert exports["t_fut_quotes"]["source"] == "auto"
    assert exports["t_account_exchange_code"]["database_name"] == "fut_mm_config"
    late_export = client.post(
        export_url,
        headers=admin_headers,
        json={"table": "t_fut_orders"},
    )
    assert late_export.status_code == 409
    assert late_export.json()["code"] == "PARSER_EXPORT_NOT_ALLOWED"


def test_parser_csv_changes_include_added_and_modified_files_but_exclude_inputs():
    before = {
        "unchanged.csv": (10, 1),
        "modified.csv": (20, 2),
        "t_fut_orders.csv": (30, 3),
    }
    after = {
        "unchanged.csv": (10, 1),
        "modified.csv": (21, 4),
        "new.csv": (40, 5),
        "t_fut_orders.csv": (31, 6),
    }

    assert workflows._changed_parser_csv_files(
        before,
        after,
        {"t_fut_orders.csv"},
    ) == ["modified.csv", "new.csv"]


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {"nested/result.csv": [10, 1]},
        {"result.csv": [10]},
        {"result.csv": [10, True]},
    ],
)
def test_parser_csv_snapshot_rejects_missing_or_malformed_state(snapshot):
    with pytest.raises(workflows.WorkflowError) as invalid:
        workflows._parse_parser_csv_snapshot(snapshot)

    assert invalid.value.code == "PARSER_REMOTE_SNAPSHOT_INVALID"
