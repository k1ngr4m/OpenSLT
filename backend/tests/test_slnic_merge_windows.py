from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.database import SessionLocal
from app.models import Resource, ScenarioWorkflowNode, TestRun as RunModel
from app.services import workflows
from app.services.orchestration import complete_workflow_step
from app.services.slnic_merge import build_windows_editcap_details, linux_home_path_to_unc
from app.workflow_node_configs import (
    DEFAULT_EDITCAP_PATH,
    SLNIC_LEGACY_MERGE_DEFAULT_COMMANDS,
    SlnicMergeConfig,
)
from conftest import create_plan_scenario, create_resource, publish_workflow


def create_parser_resource(client, headers, *, path: str = "/home/user0/parser") -> dict:
    response = client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "name": "Parser-Windows-Merge",
            "resource_type": "parser",
            "business_code": "fut_mm",
            "host": "10.1.51.210",
            "ssh_port": 22,
            "username": "tester",
            "auth_type": "password",
            "password": "secret",
            "remote_path": path,
            "capabilities": {"parser_tool": "soft_dce_speed_analysis_v7"},
            "version_info": "test",
            "notes": "",
            "is_enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_merge_run(client, headers) -> tuple[dict, dict, dict]:
    slnic = create_resource(client, headers, "SLNIC-Windows-Merge", resource_type="slnic")
    parser = create_parser_resource(client, headers)
    with SessionLocal() as db:
        stored_slnic = db.get(Resource, slnic["id"])
        stored_slnic.host = "10.1.51.210"
        stored_slnic.remote_path = "/home/user0/slnic/SLNIC NF11"
        db.commit()
    plan, scenario = create_plan_scenario(
        client,
        headers,
        required_types=["slnic", "parser"],
        resource_ids=[slnic["id"], parser["id"]],
    )
    publish_workflow(
        client,
        headers,
        scenario,
        [slnic["id"], parser["id"]],
        [{
            "node_key": "slnic-merge",
            "node_type": "slnic_merge_capture",
            "name": "合并 pcapng",
            "config": {},
        }],
    )
    created = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "plan_id": plan["id"],
            "scenario_id": scenario["id"],
            "resource_ids": [slnic["id"], parser["id"]],
            "timeout_minutes": 30,
        },
    )
    assert created.status_code == 201, created.text
    return slnic, parser, created.json()


def test_slnic_merge_config_defaults_to_linux_merge_and_windows_editcap() -> None:
    config = SlnicMergeConfig()

    assert config.commands == ["./pcap_merge_tool slnic*"]
    assert config.editcap_path == r"D:\Program Files\Wireshark\editcap.exe"


def test_slnic_merge_config_normalizes_only_the_exact_legacy_defaults() -> None:
    legacy = SlnicMergeConfig.model_validate(
        {"commands": list(SLNIC_LEGACY_MERGE_DEFAULT_COMMANDS)}
    )
    custom = SlnicMergeConfig.model_validate(
        {"commands": ["./pcap_merge_tool slnic*", "printf custom"]}
    )

    assert legacy.commands == ["./pcap_merge_tool slnic*"]
    assert legacy.editcap_path == DEFAULT_EDITCAP_PATH
    assert custom.commands == ["./pcap_merge_tool slnic*", "printf custom"]


@pytest.mark.parametrize(
    "path",
    [
        r"editcap.exe",
        r"\\server\Wireshark\editcap.exe",
        r"D:\Program Files\Wireshark\dumpcap.exe",
        'D:\\Wireshark\\editcap.exe" --bad',
        "D:\\Wireshark\\editcap.exe\ncalc.exe",
    ],
)
def test_slnic_merge_config_rejects_unsafe_or_non_absolute_editcap_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        SlnicMergeConfig(editcap_path=path)


def test_linux_home_path_to_unc_uses_host_and_removes_home_prefix() -> None:
    assert linux_home_path_to_unc(
        "10.1.51.210",
        "/home/user0/slnic/SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528",
    ) == (
        r"\\10.1.51.210\user0\slnic"
        r"\SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528"
    )


@pytest.mark.parametrize("path", ["", "/tmp/openslt", "/home", "/home/"])
def test_linux_home_path_to_unc_rejects_non_shared_paths(path: str) -> None:
    with pytest.raises(ValueError, match="/home/"):
        linux_home_path_to_unc("10.1.51.210", path)


def test_build_windows_editcap_details_quotes_all_three_paths() -> None:
    details = build_windows_editcap_details(
        editcap_path=r"D:\Program Files\Wireshark\editcap.exe",
        slnic_host="10.1.51.210",
        slnic_remote_path=(
            "/home/user0/slnic/"
            "SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528"
        ),
        parser_host="10.1.51.210",
        parser_remote_path="/home/user0/ckd/speed_analysis/soft_dce_speed_analysis_v7",
    )

    assert details == {
        "windows_input_path": (
            r"\\10.1.51.210\user0\slnic"
            r"\SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528"
            r"\tcpdump\merge_pcap.pcap"
        ),
        "windows_output_path": (
            r"\\10.1.51.210\user0\ckd\speed_analysis"
            r"\soft_dce_speed_analysis_v7\merge_pcap.pcapng"
        ),
        "windows_editcap_command": (
            r'"D:\Program Files\Wireshark\editcap.exe" -F pcapng '
            r'"\\10.1.51.210\user0\slnic'
            r'\SLNIC_NF11_10g_10g_911.hw_7881.driver_12671.sw_20240528'
            r'\tcpdump\merge_pcap.pcap" '
            r'"\\10.1.51.210\user0\ckd\speed_analysis'
            r'\soft_dce_speed_analysis_v7\merge_pcap.pcapng"'
        ),
    }


def test_merge_publish_requires_parser_resource(client, admin_headers) -> None:
    slnic = create_resource(client, admin_headers, "SLNIC-No-Parser", resource_type="slnic")
    with SessionLocal() as db:
        stored = db.get(Resource, slnic["id"])
        stored.remote_path = "/home/user0/slnic"
        db.commit()
    _, scenario = create_plan_scenario(
        client, admin_headers, required_types=["slnic"], resource_ids=[slnic["id"]]
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [slnic["id"]],
            "nodes": [{
                "node_key": "merge",
                "node_type": "slnic_merge_capture",
                "name": "合并 pcapng",
                "config": {},
            }],
        },
    )

    assert saved.status_code == 200, saved.text
    errors = saved.json()["validation_errors"]
    assert any(item["field"] == "parser_resource" for item in errors)


@pytest.mark.parametrize(
    ("resource_type", "field", "value", "message"),
    [
        ("parser", "is_enabled", False, "合并 pcapng 需要绑定已启用的解析工具资源"),
        ("parser", "remote_path", "/tmp/parser", "解析工具远端路径必须位于 /home/ 下"),
        ("slnic", "remote_path", "/tmp/slnic", "SLNIC 远端路径必须位于 /home/ 下"),
    ],
)
def test_merge_publish_rechecks_bound_resource_state(
    client,
    admin_headers,
    resource_type: str,
    field: str,
    value,
    message: str,
) -> None:
    slnic = create_resource(
        client, admin_headers, "SLNIC-Publish-State", resource_type="slnic"
    )
    parser = create_parser_resource(client, admin_headers)
    with SessionLocal() as db:
        stored_slnic = db.get(Resource, slnic["id"])
        stored_slnic.remote_path = "/home/user0/slnic"
        db.commit()
    _, scenario = create_plan_scenario(
        client,
        admin_headers,
        required_types=["slnic", "parser"],
        resource_ids=[slnic["id"], parser["id"]],
    )
    document = client.get(
        f"/api/v1/scenarios/{scenario['id']}/workflow", headers=admin_headers
    ).json()
    saved = client.put(
        f"/api/v1/scenarios/{scenario['id']}/workflow",
        headers=admin_headers,
        json={
            "expected_revision": document["draft"]["revision"],
            "resource_ids": [slnic["id"], parser["id"]],
            "nodes": [{
                "node_key": "merge",
                "node_type": "slnic_merge_capture",
                "name": "合并 pcapng",
                "config": {},
            }],
        },
    )
    assert saved.status_code == 200, saved.text
    resource_id = {"slnic": slnic["id"], "parser": parser["id"]}[resource_type]

    with SessionLocal() as db:
        stored = db.get(Resource, resource_id)
        setattr(stored, field, value)
        db.commit()

    published = client.post(
        f"/api/v1/scenarios/{scenario['id']}/workflow/publish",
        headers=admin_headers,
    )

    assert published.status_code == 422, published.text
    assert message in {item["message"] for item in published.json()["errors"]}


def test_run_snapshot_includes_resource_remote_paths(client, admin_headers) -> None:
    _, _, run = create_merge_run(client, admin_headers)

    snapshots = {item["type"]: item for item in run["config_snapshot"]["resources"]}
    assert snapshots["slnic"]["remote_path"] == "/home/user0/slnic/SLNIC NF11"
    assert snapshots["parser"]["remote_path"] == "/home/user0/parser"


@pytest.mark.asyncio
async def test_execute_merge_archives_old_parser_output_and_returns_windows_command(
    client, admin_headers, monkeypatch
) -> None:
    slnic, parser, run = create_merge_run(client, admin_headers)
    with SessionLocal() as db:
        stored_run = db.get(RunModel, run["id"])
        step = stored_run.steps[0]
        node = db.get(ScenarioWorkflowNode, step.workflow_node_id)
        resources = {
            item.resource_type: item
            for item in db.query(Resource)
            .filter(Resource.id.in_([slnic["id"], parser["id"]]))
            .all()
        }
        resources["slnic"].host = "10.9.9.11"
        resources["slnic"].remote_path = "/home/mutated/slnic"
        resources["parser"].host = "10.9.9.12"
        resources["parser"].remote_path = "/home/mutated/parser"
        db.flush()

        events: list[tuple[str, str]] = []

        class ParserSFTP:
            async def makedirs(self, path, exist_ok=False):
                events.append(("makedirs", path))

            async def posix_rename(self, source, target):
                events.append(("rename", f"{source}|{target}"))

            def exit(self):
                return None

        class ParserConnection:
            async def start_sftp_client(self):
                return ParserSFTP()

            def close(self):
                return None

            async def wait_closed(self):
                return None

        class SlnicConnection:
            async def run(self, command, check=False):
                events.append(("run", command))
                return SimpleNamespace(exit_status=0, stdout="", stderr="")

            def close(self):
                return None

            async def wait_closed(self):
                return None

        connections = iter([ParserConnection(), SlnicConnection()])

        async def fake_connect(**_options):
            return next(connections)

        monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
        summary = await workflows.execute_slnic_node(db, stored_run, step, node, resources)

    assert events[0][0] == "makedirs"
    assert events[1][0] == "rename"
    assert events[1][1].startswith("/home/user0/parser/merge_pcap.pcapng|")
    assert events[2][0] == "run"
    assert summary["parser_resource_id"] == parser["id"]
    assert summary["windows_editcap_command"] == (
        '"D:\\Program Files\\Wireshark\\editcap.exe" -F pcapng '
        '"\\\\10.1.51.210\\user0\\slnic\\SLNIC NF11\\tcpdump\\merge_pcap.pcap" '
        '"\\\\10.1.51.210\\user0\\parser\\merge_pcap.pcapng"'
    )
    assert "./editcap" not in events[2][1]


@pytest.mark.asyncio
async def test_execute_merge_does_not_run_linux_command_when_archive_fails(
    client, admin_headers, monkeypatch
) -> None:
    slnic, parser, run = create_merge_run(client, admin_headers)
    with SessionLocal() as db:
        stored_run = db.get(RunModel, run["id"])
        step = stored_run.steps[0]
        node = db.get(ScenarioWorkflowNode, step.workflow_node_id)
        resources = {
            item.resource_type: item
            for item in db.query(Resource)
            .filter(Resource.id.in_([slnic["id"], parser["id"]]))
            .all()
        }
        connect_calls: list[dict] = []

        class FailingParserSFTP:
            async def makedirs(self, _path, exist_ok=False):
                return None

            async def posix_rename(self, _source, _target):
                raise PermissionError("archive denied")

            def exit(self):
                return None

        class ParserConnection:
            async def start_sftp_client(self):
                return FailingParserSFTP()

            def close(self):
                return None

            async def wait_closed(self):
                return None

        async def fake_connect(**_options):
            connect_calls.append(_options)
            return ParserConnection()

        monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)

        with pytest.raises(
            workflows.WorkflowError,
            match="归档解析目录中的旧 merge_pcap.pcapng 失败",
        ):
            await workflows.execute_slnic_node(db, stored_run, step, node, resources)

    assert len(connect_calls) == 1


@pytest.mark.asyncio
async def test_collect_merge_uses_parser_root_and_rejects_empty_file(
    client, admin_headers, monkeypatch
) -> None:
    _, parser, run = create_merge_run(client, admin_headers)
    with SessionLocal() as db:
        stored_run = db.get(RunModel, run["id"])
        step = stored_run.steps[0]
        parser_resource = db.get(Resource, parser["id"])

        class EmptySFTP:
            async def get(self, remote_path, local_path):
                assert remote_path == "/home/user0/parser/merge_pcap.pcapng"
                Path(local_path).write_bytes(b"")

            def exit(self):
                return None

        class Connection:
            async def start_sftp_client(self):
                return EmptySFTP()

            def close(self):
                return None

            async def wait_closed(self):
                return None

        async def fake_connect(**_):
            return Connection()

        monkeypatch.setattr(workflows.asyncssh, "connect", fake_connect)
        with pytest.raises(workflows.WorkflowError, match="不能为空"):
            await workflows.collect_slnic_merge_artifact(
                db,
                stored_run,
                step,
                parser_resource,
                remote_path="/home/user0/parser",
            )
