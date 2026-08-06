from __future__ import annotations

import ntpath
import posixpath
import typing
from contextlib import suppress
from uuid import uuid4

import asyncssh

from app.core.time import beijing_now
from app.models import Resource, RunStep, TestRun
from app.services.workflow_capture import _ssh_options
from app.services.workflow_core import WorkflowError


def linux_home_path_to_unc(host: str, remote_path: str) -> str:
    clean_host = str(host or "").strip().strip("\\/")
    clean_path = str(remote_path or "").strip().rstrip("/")
    if not clean_host or not clean_path.startswith("/home/") or clean_path == "/home/":
        raise ValueError("资源远端路径必须位于 /home/ 下才能转换为 Windows UNC 路径")
    relative = clean_path[len("/home/") :]
    if not relative or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("资源远端路径必须位于 /home/ 下才能转换为 Windows UNC 路径")
    return "\\\\" + clean_host + "\\" + relative.replace("/", "\\")


def build_windows_editcap_details(
    *,
    editcap_path: str,
    slnic_host: str,
    slnic_remote_path: str,
    parser_host: str,
    parser_remote_path: str,
) -> typing.Dict[str, str]:
    slnic_root = linux_home_path_to_unc(slnic_host, slnic_remote_path)
    parser_root = linux_home_path_to_unc(parser_host, parser_remote_path)
    input_path = ntpath.join(slnic_root, "tcpdump", "merge_pcap.pcap")
    output_path = ntpath.join(parser_root, "merge_pcap.pcapng")
    return {
        "windows_input_path": input_path,
        "windows_output_path": output_path,
        "windows_editcap_command": (
            f'"{editcap_path}" -F pcapng "{input_path}" "{output_path}"'
        ),
    }


def resource_snapshot(
    run: TestRun,
    resource_type: str,
    fallback: Resource,
) -> typing.Dict[str, typing.Any]:
    snapshots = (run.config_snapshot or {}).get("resources") or []
    snapshot = next(
        (
            item
            for item in snapshots
            if isinstance(item, dict) and item.get("type") == resource_type
        ),
        {},
    )
    return {
        "id": snapshot.get("id") or fallback.id,
        "host": str(snapshot.get("host") or fallback.host).strip(),
        "remote_path": str(snapshot.get("remote_path") or fallback.remote_path).strip(),
    }


async def archive_previous_parser_output(
    run: TestRun,
    step: RunStep,
    parser_resource: Resource,
    *,
    remote_path: str,
) -> typing.Optional[str]:
    root = remote_path.rstrip("/")
    source = posixpath.join(root, "merge_pcap.pcapng")
    archive_directory = posixpath.join(root, ".openslt-archive")
    timestamp = beijing_now().strftime("%Y%m%d%H%M%S")
    archive_name = (
        f"merge_pcap.{run.run_number}.step-{step.id}.{timestamp}.{uuid4().hex[:8]}.pcapng"
    )
    target = posixpath.join(archive_directory, archive_name)
    connection = None
    sftp = None
    try:
        connection = await asyncssh.connect(**_ssh_options(parser_resource))
        sftp = await connection.start_sftp_client()
        await sftp.makedirs(archive_directory, exist_ok=True)
        try:
            await sftp.posix_rename(source, target)
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
            return None
        return target
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError(
            "SLNIC_PREVIOUS_OUTPUT_ARCHIVE_FAILED",
            f"归档解析目录中的旧 merge_pcap.pcapng 失败：{exc}",
            409,
        ) from exc
    finally:
        if sftp:
            with suppress(Exception):
                sftp.exit()
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def prepare_slnic_merge_execution(
    run: TestRun,
    step: RunStep,
    slnic_resource: Resource,
    parser_resource: Resource,
    *,
    editcap_path: str,
) -> typing.Dict[str, typing.Any]:
    slnic_snapshot = resource_snapshot(run, "slnic", slnic_resource)
    parser_snapshot = resource_snapshot(run, "parser", parser_resource)
    try:
        windows_details = build_windows_editcap_details(
            editcap_path=editcap_path,
            slnic_host=str(slnic_snapshot["host"]),
            slnic_remote_path=str(slnic_snapshot["remote_path"]),
            parser_host=str(parser_snapshot["host"]),
            parser_remote_path=str(parser_snapshot["remote_path"]),
        )
    except ValueError as exc:
        raise WorkflowError("SLNIC_UNC_PATH_INVALID", str(exc), 409) from exc
    archived = await archive_previous_parser_output(
        run,
        step,
        parser_resource,
        remote_path=str(parser_snapshot["remote_path"]),
    )
    return {
        **windows_details,
        "parser_resource_id": parser_resource.id,
        "parser_remote_path": parser_snapshot["remote_path"],
        "previous_output_archive_path": archived,
    }
