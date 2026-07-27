from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import asyncssh

from app.models import Artifact
from app.services import order_configs
from app.services import orchestration, workflows
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


def create_database_resource(client, headers):
    response = client.post("/api/v1/resources", headers=headers, json={
        "name": "Parser-Database", "resource_type": "database", "business_code": "fut_mm",
        "host": "", "ssh_port": 22, "username": "", "auth_type": "password",
        "database_engine": "mysql", "database_connection_mode": "direct",
        "database_host": "127.0.0.1", "database_port": 3306,
        "database_names": ["fut_mm_trading_data"], "database_username": "tester",
        "database_password": "secret", "database_tls_enabled": False,
        "remote_path": "", "capabilities": {}, "version_info": "test",
        "notes": "", "is_enabled": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def parser_nodes():
    return [
        {"node_key": "start", "node_type": "slnic_start_capture", "name": "Start", "config": {}},
        {"node_key": "stop", "node_type": "slnic_stop_capture", "name": "Stop", "config": {}},
        {"node_key": "merge", "node_type": "slnic_merge_capture", "name": "Merge", "config": {}},
        {
            "node_key": "parse", "node_type": "parser_parse", "name": "Parse",
            "config": {"database_name": "fut_mm_trading_data"},
        },
    ]


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


def test_parser_publish_rejects_missing_merge(client, admin_headers):
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
    assert any("pcapng" in item["message"] for item in published.json()["errors"])


def test_remote_parser_uploads_inputs_executes_and_downloads_changed_csv(
    client, admin_headers, monkeypatch
):
    config_sftp = FakeConfigSFTP()

    async def fake_config_connect(**_options):
        return FakeConfigConnection(config_sftp)

    monkeypatch.setattr(order_configs.asyncssh, "connect", fake_config_connect)
    slnic = create_resource(client, admin_headers, "SLNIC-Remote-Parser", resource_type="slnic")
    database = create_database_resource(client, admin_headers)
    parser = create_parser_resource(client, admin_headers)
    plan, scenario = create_plan_scenario(
        client, admin_headers, resource_ids=[slnic["id"], database["id"], parser["id"]]
    )
    publish_workflow(
        client, admin_headers, scenario,
        [slnic["id"], database["id"], parser["id"]], parser_nodes(),
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

    async def fake_export(database_resource, database_name, table, target):
        target.write_text("id,account\n1,100001\n", encoding="utf-8")
        return 1

    class FakeSFTP:
        def __init__(self):
            self.files = {"/home/user0/soft_cffex_speed_analysis_v2/existing.csv": b"old"}
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
            self.commands = []
            self.closed = False

        async def start_sftp_client(self):
            return self.sftp

        async def run(self, command, check=False):
            self.commands.append(command)
            output_path = "/home/user0/soft_cffex_speed_analysis_v2/analysis-result.csv"
            self.sftp.files[output_path] = b"sequence,latency_us\n1,82.1\n"
            self.sftp.mtimes[output_path] = 3
            return SimpleNamespace(exit_status=0, stdout="finished", stderr="")

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    connection = FakeConnection()

    async def fake_connect(**kwargs):
        return connection

    monkeypatch.setattr(workflows, "execute_slnic_node", fake_slnic)
    monkeypatch.setattr(workflows, "_export_parser_table", fake_export)
    monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)

    created = client.post("/api/v1/runs", headers=admin_headers, json={
        "plan_id": plan["id"], "scenario_id": scenario["id"],
        "resource_ids": [slnic["id"], database["id"], parser["id"]],
        "timeout_minutes": 30,
    }).json()
    client.post(f"/api/v1/runs/{created['id']}/start", headers=admin_headers)
    run = complete_workflow(client, admin_headers, created["id"])
    assert run["status"] == "completed"
    parsed = [item for item in run["artifacts"] if item["artifact_type"] == "parsed_csv"]
    assert [item["name"] for item in parsed] == ["analysis-result.csv"]
    assert connection.commands == [
        "cd /home/user0/soft_cffex_speed_analysis_v2 && "
        "./soft_cffex_speed_analysis_v2 soft_cffex_speed_analysis.xml"
    ]
    assert connection.closed is True
    assert connection.sftp.closed is True
