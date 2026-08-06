from __future__ import annotations

import typing
import asyncio
import posixpath
import shlex
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

import asyncssh
import jwt
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.logging import trace_id_ctx, user_id_ctx
from app.core.security import CredentialSecretError, decode_token, decrypt_secret
from app.core.time import beijing_now
from app.models import Resource, RunStep, ScenarioWorkflowNode, ScenarioWorkflowVersion, TestRun, User
from app.services.audit import write_audit
from app.services.events import broker
from app.services.orchestration import append_log
from app.services.resource_relations import run_resource_ids
from app.services.order_sessions import order_session_name
from app.services.run_state import transition_run, transition_step
from app.services.slnic_merge import prepare_slnic_merge_execution
from app.services.workflow_handlers import registry as workflow_handler_registry
from app.services.workflow_handlers.slnic import SLNIC_TERMINAL_COMMANDS
from app.workflow_node_configs import PARSER_ACTIONS, MarketStartupConfig, ShellCommandsConfig, SlnicMergeConfig, parse_node_config


TERMINAL_RESOURCE_TYPES = {"rem", "market", "order", "slnic", "parser"}
SHELL_TERMINAL_COMMANDS = {
    "rem_startup": {
        "resource_type": "rem",
        "action": "启动",
        "node_label": "启动 REM 柜台",
        "workdir_suffix": None,
    },
    **{
        node_type: {
            **metadata,
            "resource_type": "slnic",
            "workdir_suffix": "tcpdump",
        }
        for node_type, metadata in SLNIC_TERMINAL_COMMANDS.items()
    },
}
MAX_INPUT_SIZE = 64 * 1024
MIN_COLUMNS = 20
MAX_COLUMNS = 300
MIN_ROWS = 5
MAX_ROWS = 120


@dataclass
class TerminalResource:
    id: int
    name: str
    resource_type: str
    host: str
    port: int
    username: str
    password: typing.Union[str, None]
    private_key: typing.Union[str, None]
    remote_path: str
    capabilities: typing.Dict[str, typing.Any] = field(default_factory=dict)


@dataclass
class ParserTerminalLease:
    run_id: typing.Optional[int] = None
    step_id: typing.Optional[int] = None


def parser_actions_for_resource(resource: typing.Any) -> typing.Tuple[str, ...]:
    configured = (getattr(resource, "capabilities", None) or {}).get("parser_actions")
    if configured is None:
        return PARSER_ACTIONS
    if not isinstance(configured, list):
        return ()
    return tuple(action for action in configured if action in PARSER_ACTIONS)


def build_parser_start_command(workdir: str, binary_path: str, config_filename: str) -> str:
    return (
        f"cd {shlex.quote(workdir)} && "
        f"{shlex.quote(binary_path)} {shlex.quote(config_filename)}"
    )


def _clamp(value: object, minimum: int, maximum: int, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


async def _send(websocket: WebSocket, payload: dict) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, WebSocketDisconnect):
        return False


async def _close(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code=code)


def _audit(
    websocket: WebSocket,
    actor_id: int,
    resource_id: int,
    action: str,
    result: str = "success",
    detail: typing.Union[dict, None] = None,
) -> None:
    db = SessionLocal()
    try:
        actor = db.get(User, actor_id)
        write_audit(
            db,
            action,
            "resource",
            resource_id,
            actor,
            websocket,  # WebSocket exposes the same client and headers used by audit metadata.
            result=result,
            detail=detail,
        )
        db.commit()
    finally:
        db.close()


def _load_context(token: str, resource_id: int) -> typing.Union[typing.Tuple[int, TerminalResource], typing.Tuple[None, str]]:
    try:
        payload = decode_token(token, "access")
        actor_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        return None, "登录凭据无效或已过期"

    db = SessionLocal()
    try:
        actor = db.get(User, actor_id)
        if not actor or not actor.is_active:
            return None, "登录凭据无效或已过期"
        if actor.role not in {"admin", "tester"}:
            return None, "当前用户无权使用资源操作台"

        resource = db.get(Resource, resource_id)
        if not resource or resource.is_deleted:
            return None, "资源不存在"
        if resource.resource_type not in TERMINAL_RESOURCE_TYPES:
            return None, "该资源类型不支持 SSH 操作台"
        if not resource.is_enabled:
            return None, "资源已停用，无法打开操作台"

        return actor.id, TerminalResource(
            id=resource.id,
            name=resource.name,
            resource_type=resource.resource_type,
            host=resource.host,
            port=resource.ssh_port,
            username=resource.username,
            password=decrypt_secret(resource.encrypted_password),
            private_key=decrypt_secret(resource.encrypted_private_key),
            remote_path=resource.remote_path,
            capabilities=dict(resource.capabilities or {}),
        )
    finally:
        db.close()


def _remote_command(resource: TerminalResource) -> typing.Union[str, None]:
    if not resource.remote_path.strip():
        return None
    path = shlex.quote(resource.remote_path.strip())
    return (
        f"if cd -- {path} 2>/dev/null; then :; "
        "else printf '\\r\\nOpenSLT: configured remote path is unavailable; using home directory.\\r\\n'; cd ~; fi; "
        'exec "${SHELL:-/bin/sh}" -l'
    )


def _workflow_command_error(code: str, message: str) -> dict:
    return {"type": "workflow_command", "status": "failed", "code": code, "message": message}


def _parser_action_error(code: str, message: str) -> dict:
    return {"type": "parser_action", "status": "failed", "code": code, "message": message}


def _duration_ms(started_at: typing.Union[datetime, None], finished_at: datetime) -> int:
    if not started_at:
        return 0
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _parse_workflow_command_ids(run_id: object, step_id: object) -> typing.Tuple[typing.Union[int, None], typing.Union[int, None], typing.Union[dict, None]]:
    try:
        return int(run_id), int(step_id), None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, None, _workflow_command_error("INVALID_WORKFLOW_STEP", "运行或节点参数无效")


def _validate_terminal_step(
    db,
    *,
    resource: TerminalResource,
    run_id: object,
    step_id: object,
    operation: object,
) -> typing.Tuple[typing.Union[TestRun, None], typing.Union[RunStep, None], bool, typing.Union[dict, None]]:
    if operation not in {"start", "retry"}:
        return None, None, False, _workflow_command_error("INVALID_OPERATION", "仅支持启动或重试当前节点")
    parsed_run_id, parsed_step_id, error = _parse_workflow_command_ids(run_id, step_id)
    if error:
        return None, None, False, error
    run = db.get(TestRun, parsed_run_id, options=[selectinload(TestRun.steps), selectinload(TestRun.resource_links)])
    if not run:
        return None, None, False, _workflow_command_error("RUN_NOT_FOUND", "运行不存在")
    if resource.id not in run_resource_ids(run):
        return None, None, False, _workflow_command_error("INVALID_RESOURCE", "当前资源不属于该运行")
    current = next((item for item in run.steps if item.status != "succeeded"), None)
    step = next((item for item in run.steps if item.id == parsed_step_id), None)
    if not current or not step or current.id != step.id:
        return None, None, False, _workflow_command_error("INVALID_WORKFLOW_STEP", "只能操作当前节点")

    retrying = operation == "retry"
    expected_run_status = "awaiting_step_retry" if retrying else "awaiting_step_start"
    expected_step_status = "failed" if retrying else "pending"
    if run.status != expected_run_status or step.status != expected_step_status:
        return None, None, False, _workflow_command_error("INVALID_TRANSITION", "当前状态不能通过终端执行该节点")
    return run, step, retrying, None


def _build_terminal_command(workdir: str, commands: typing.Sequence[str]) -> str:
    body = "\n".join(commands)
    return f"cd {shlex.quote(workdir)} && {{\n{body}\n}}"


def _build_chained_terminal_command(workdir: str, commands: typing.Sequence[str]) -> str:
    body = " &&\n".join(commands)
    return f"cd {shlex.quote(workdir)} && {{\n{body}\n}}"


def _record_terminal_command_dispatch(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run: TestRun,
    step: RunStep,
    retrying: bool,
    action: str,
    workdir: str,
    commands: typing.Sequence[str],
    command: str,
    result_extra: typing.Optional[dict] = None,
    response_extra: typing.Optional[dict] = None,
) -> typing.Tuple[str, dict]:
    now = beijing_now()
    command_list = list(commands)
    if retrying:
        step.retry_count += 1
    transition_step(step, "waiting")
    step.progress = 100
    step.started_at = now
    step.finished_at = None
    step.duration_ms = 0
    step.error_message = None
    step.result_summary = {
        "resource_id": resource.id,
        "resource_name": resource.name,
        "remote_workdir": workdir,
        "commands": command_list,
        "command": command,
        "mode": "terminal",
        "exit_code": None,
        "dispatched_by": actor_id,
        "dispatched_at": now.isoformat(),
        **(result_extra or {}),
    }
    transition_run(run, "awaiting_step_completion")
    run.progress = int((step.position - 1) * 100 / max(1, len(run.steps)))
    run.error_code = None
    run.error_message = None
    append_log(
        db,
        run,
        "workflow.step_retried" if retrying else "workflow.step_started",
        f"{step.name}{'重试' if retrying else '开始'}，已通过 SSH 终端下发{action}指令",
        step=step,
        source="terminal",
        detail={"retry_count": step.retry_count, "mode": "terminal", "action": action},
    )
    append_log(
        db,
        run,
        "workflow.step_executed",
        f"{step.name}{action}指令已在终端下发，等待手动完成",
        step=step,
        source="terminal",
        detail={
            "command": command,
            "commands": command_list,
            "remote_workdir": workdir,
            "resource_id": resource.id,
            "mode": "terminal",
            "action": action,
            **(response_extra or {}),
        },
        log_type="remote_command",
    )
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    return command, {
        "type": "workflow_command",
        "status": "dispatched",
        "command": command,
        "run_id": run.id,
        "step_id": step.id,
        "resource_id": resource.id,
        "commands": command_list,
        **(response_extra or {}),
    }


def _dispatch_shell_terminal_command(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run: TestRun,
    step,
    retrying: bool,
) -> typing.Tuple[typing.Union[str, None], dict]:
    command_meta = SHELL_TERMINAL_COMMANDS.get(step.node_type)
    if not command_meta:
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "当前节点不支持通过交互终端执行")
    expected_resource_type = str(command_meta["resource_type"])
    if resource.resource_type != expected_resource_type:
        return None, _workflow_command_error("INVALID_RESOURCE", "当前终端资源与工作流节点不匹配")
    if not resource.remote_path.strip():
        return None, _workflow_command_error(
            f"{expected_resource_type.upper()}_REMOTE_PATH_REQUIRED",
            f"{expected_resource_type.upper()} 资源未配置远端路径",
        )
    try:
        config = typing.cast(
            ShellCommandsConfig,
            parse_node_config(step.node_type, step.config_snapshot or {}),
        )
    except Exception:
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "工作流节点命令配置无效")
    if not config.commands:
        return None, _workflow_command_error("SHELL_COMMANDS_REQUIRED", "工作流节点至少需要一条命令")

    root = resource.remote_path.strip().rstrip("/") or "/"
    suffix = command_meta.get("workdir_suffix")
    workdir = posixpath.join(root, str(suffix)) if suffix else root
    action = str(command_meta["action"])
    commands = list(config.commands)
    command = _build_terminal_command(workdir, commands)
    return _record_terminal_command_dispatch(
        db=db,
        actor_id=actor_id,
        resource=resource,
        run=run,
        step=step,
        retrying=retrying,
        action=action,
        workdir=workdir,
        commands=commands,
        command=command,
    )


async def _dispatch_market_terminal_command(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    connection: asyncssh.SSHClientConnection,
    run: TestRun,
    step: RunStep,
    retrying: bool,
) -> typing.Tuple[typing.Union[str, None], dict]:
    if resource.resource_type != "market":
        return None, _workflow_command_error("INVALID_RESOURCE", "当前终端不是模拟市场资源")
    if not resource.remote_path.strip():
        return None, _workflow_command_error(
            "MARKET_REMOTE_PATH_REQUIRED", "模拟市场资源未配置远端路径"
        )
    try:
        config = typing.cast(
            MarketStartupConfig,
            parse_node_config(step.node_type, step.config_snapshot or {}),
        )
    except Exception:
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "模拟市场节点脚本配置无效")
    if not config.scripts:
        return None, _workflow_command_error(
            "MARKET_SCRIPTS_REQUIRED", "启动模拟市场至少需要一个脚本"
        )

    root = resource.remote_path.strip().rstrip("/") or "/"
    sftp = None
    try:
        sftp = await connection.start_sftp_client()
        for selection in config.scripts:
            path = posixpath.join(root, selection.filename)
            try:
                attrs = await sftp.lstat(path)
            except (asyncssh.SFTPNoSuchFile, FileNotFoundError):
                return None, _workflow_command_error(
                    "MARKET_SCRIPT_NOT_FOUND", f"模拟市场脚本 {selection.filename} 不存在"
                )
            if attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                return None, _workflow_command_error(
                    "MARKET_SCRIPT_INVALID", f"模拟市场脚本 {selection.filename} 必须是普通文件"
                )
            if not (attrs.permissions or 0) & 0o111:
                return None, _workflow_command_error(
                    "MARKET_SCRIPT_NOT_EXECUTABLE",
                    f"模拟市场脚本 {selection.filename} 没有执行权限",
                )
    except (asyncssh.Error, OSError) as exc:
        return None, _workflow_command_error(
            "MARKET_SCRIPT_SFTP_FAILED", f"检查远端模拟市场脚本失败：{exc}"
        )
    finally:
        if sftp:
            with suppress(Exception):
                sftp.exit()

    scripts = [selection.filename for selection in config.scripts]
    commands = [shlex.quote(f"./{filename}") for filename in scripts]
    command = _build_chained_terminal_command(root, commands)
    return _record_terminal_command_dispatch(
        db=db,
        actor_id=actor_id,
        resource=resource,
        run=run,
        step=step,
        retrying=retrying,
        action="启动",
        workdir=root,
        commands=commands,
        command=command,
        result_extra={"scripts": scripts},
        response_extra={"scripts": scripts},
    )


async def _dispatch_slnic_merge_terminal_command(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run: TestRun,
    step: RunStep,
    retrying: bool,
) -> typing.Tuple[typing.Union[str, None], dict]:
    try:
        config = typing.cast(
            SlnicMergeConfig,
            parse_node_config(step.node_type, step.config_snapshot or {}),
        )
    except Exception:
        return None, _workflow_command_error(
            "INVALID_WORKFLOW_STEP", "合并 pcapng 节点配置无效"
        )
    if not config.commands:
        return None, _workflow_command_error(
            "SHELL_COMMANDS_REQUIRED", "工作流节点至少需要一条命令"
        )
    parser_resource = db.scalar(
        select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "parser",
            Resource.is_deleted.is_(False),
            Resource.is_enabled.is_(True),
        )
    )
    slnic_resource = db.get(Resource, resource.id)
    if not parser_resource or not slnic_resource:
        return None, _workflow_command_error(
            "PARSER_RESOURCE_REQUIRED", "合并 pcapng 需要已启用的解析工具资源"
        )
    try:
        merge_details = await prepare_slnic_merge_execution(
            run,
            step,
            slnic_resource,
            parser_resource,
            editcap_path=config.editcap_path,
        )
    except WorkflowError as exc:
        return None, _workflow_command_error(exc.code, exc.message)
    workdir = posixpath.join(resource.remote_path.strip().rstrip("/"), "tcpdump")
    commands = list(config.commands)
    command = _build_terminal_command(workdir, commands)
    return _record_terminal_command_dispatch(
        db=db,
        actor_id=actor_id,
        resource=resource,
        run=run,
        step=step,
        retrying=retrying,
        action="合并",
        workdir=workdir,
        commands=commands,
        command=command,
        result_extra=merge_details,
        response_extra=merge_details,
    )


async def _dispatch_parser_terminal_command(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run: TestRun,
    step: RunStep,
    retrying: bool,
    connection: asyncssh.SSHClientConnection,
    lease: ParserTerminalLease,
) -> typing.Tuple[typing.Union[str, None], dict]:
    from app.services import workflows

    if resource.resource_type != "parser":
        return None, _workflow_command_error("INVALID_RESOURCE", "当前终端不是解析工具资源")
    if not run.workflow_version_id or not step.workflow_node_id:
        return None, _workflow_command_error("WORKFLOW_NOT_FOUND", "运行关联的工作流不存在")
    workflow = db.get(ScenarioWorkflowVersion, run.workflow_version_id)
    node = db.get(ScenarioWorkflowNode, step.workflow_node_id)
    if not workflow or not node or node.workflow_version_id != workflow.id or step.node_type != "parser_parse":
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "当前节点不是数据解析节点")
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(run_resource_ids(run)))).all())
    run_resources = {item.resource_type: item for item in resources}
    parser_resource = run_resources.get("parser")
    if not parser_resource or parser_resource.id != resource.id:
        return None, _workflow_command_error("INVALID_RESOURCE", "当前解析资源不属于该运行")

    now = beijing_now()
    if retrying:
        step.retry_count += 1
    transition_step(step, "running")
    step.progress = 0
    step.started_at = now
    step.finished_at = None
    step.duration_ms = None
    step.error_message = None
    transition_run(run, "running")
    run.error_code = None
    run.error_message = None
    db.flush()
    try:
        summary = await workflows.prepare_parser_terminal_node(
            db,
            run,
            step,
            node,
            run_resources,
            connection,
            append_log_callback=append_log,
        )
        binary = str((parser_resource.capabilities or {}).get("parser_binary") or (parser_resource.capabilities or {}).get("parser_tool") or "").strip()
        analysis_filename = str((summary.get("parser_xml_files") or {}).get("analysis", {}).get("filename") or "").strip()
        command = build_parser_start_command(
            str(summary["remote_workdir"]),
            posixpath.join(parser_resource.remote_path.rstrip("/"), binary),
            analysis_filename,
        )
        history = list((step.result_summary or {}).get("parser_action_history") or [])[-100:]
        summary.update({
            "command": command,
            "parser_action_history": history,
            "supported_parser_actions": list(parser_actions_for_resource(parser_resource)),
            "dispatched_by": actor_id,
            "dispatched_at": now.isoformat(),
        })
    except Exception as exc:
        failed_at = beijing_now()
        message = getattr(exc, "message", str(exc))
        code = getattr(exc, "code", "PARSER_PREPARATION_FAILED")
        transition_step(step, "failed")
        step.progress = 0
        step.error_message = message
        step.finished_at = failed_at
        step.duration_ms = _duration_ms(step.started_at, failed_at)
        transition_run(run, "awaiting_step_retry")
        run.finished_at = None
        append_log(
            db,
            run,
            "workflow.step_failed",
            message,
            level="ERROR",
            step=step,
            source="terminal",
            detail={"error_code": code, "mode": "terminal"},
        )
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
        return None, _workflow_command_error(code, message)

    step.result_summary = summary
    transition_step(step, "waiting")
    step.progress = 100
    step.finished_at = None
    step.duration_ms = _duration_ms(step.started_at, beijing_now())
    transition_run(run, "awaiting_step_completion")
    run.progress = int((step.position - 1) * 100 / max(1, len(run.steps)))
    append_log(
        db,
        run,
        "workflow.step_retried" if retrying else "workflow.step_started",
        f"{step.name}{'重试' if retrying else '开始'}，已通过 SSH 终端准备解析输入",
        step=step,
        source="terminal",
        detail={"retry_count": step.retry_count, "mode": "terminal"},
    )
    append_log(
        db,
        run,
        "workflow.step_executed",
        f"{step.name}启动指令已在终端下发，等待手动解析",
        step=step,
        source="terminal",
        detail={"command": command, "remote_workdir": summary["remote_workdir"], "resource_id": resource.id, "mode": "terminal"},
        log_type="remote_command",
    )
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    lease.run_id = run.id
    lease.step_id = step.id
    return command, {
        "type": "workflow_command",
        "status": "dispatched",
        "command": command,
        "run_id": run.id,
        "step_id": step.id,
        "resource_id": resource.id,
        "supported_parser_actions": list(parser_actions_for_resource(parser_resource)),
    }


async def _dispatch_parser_action(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run_id: object,
    step_id: object,
    action: object,
    lease: ParserTerminalLease,
) -> dict:
    parsed_run_id, parsed_step_id, error = _parse_workflow_command_ids(run_id, step_id)
    if error:
        return _parser_action_error(error["code"], error["message"])
    if lease.run_id != parsed_run_id or lease.step_id != parsed_step_id:
        return _parser_action_error("INVALID_WORKFLOW_STEP", "当前终端没有对应的解析节点")
    if not isinstance(action, str) or action not in parser_actions_for_resource(resource):
        return _parser_action_error("PARSER_ACTION_NOT_ALLOWED", "当前解析资源未配置该指令")
    run = db.get(TestRun, parsed_run_id, options=[selectinload(TestRun.steps), selectinload(TestRun.resource_links)])
    if not run or resource.id not in run_resource_ids(run):
        return _parser_action_error("INVALID_RESOURCE", "当前资源不属于该运行")
    step = next((item for item in run.steps if item.id == parsed_step_id), None)
    if not step or step.node_type != "parser_parse" or step.status != "waiting" or run.status != "awaiting_step_completion":
        return _parser_action_error("INVALID_TRANSITION", "当前状态不能发送解析指令")
    return {
        "type": "parser_action",
        "status": "dispatched",
        "run_id": run.id,
        "step_id": step.id,
        "action": action,
    }


def _record_parser_action_dispatch(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run_id: int,
    step_id: int,
    action: str,
) -> None:
    run = db.get(TestRun, run_id, options=[selectinload(TestRun.steps)])
    step = next((item for item in run.steps if item.id == step_id), None) if run else None
    if not run or not step:
        return
    now = beijing_now()
    history = list((step.result_summary or {}).get("parser_action_history") or [])[-99:]
    history.append({
        "action": action,
        "status": "dispatched",
        "requested_by": actor_id,
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
    })
    step.result_summary = {**(step.result_summary or {}), "parser_action_history": history}
    append_log(
        db,
        run,
        "parser.action_dispatched",
        f"已通过 SSH 终端发送解析指令 {action}",
        step=step,
        source="terminal",
        detail={"action": action, "resource_id": resource.id, "mode": "terminal"},
        log_type="remote_command",
    )
    db.commit()


async def _dispatch_order_preparation_command(
    *,
    db,
    actor_id: int,
    resource: TerminalResource,
    run: TestRun,
    step,
    retrying: bool,
) -> typing.Tuple[typing.Union[str, None], dict]:
    from app.services.workflows import WorkflowError, prepare_order_node

    if resource.resource_type != "order":
        return None, _workflow_command_error("INVALID_RESOURCE", "当前终端不是发单资源")
    if step.node_type != "order_preparation":
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "当前节点不是发单准备节点")
    if not run.workflow_version_id or not step.workflow_node_id:
        return None, _workflow_command_error("WORKFLOW_NOT_FOUND", "运行关联的工作流不存在")

    workflow = db.get(ScenarioWorkflowVersion, run.workflow_version_id)
    node = db.get(ScenarioWorkflowNode, step.workflow_node_id)
    if not workflow or not node or node.workflow_version_id != workflow.id:
        return None, _workflow_command_error("WORKFLOW_NODE_NOT_FOUND", "发单节点不存在")
    resources = list(db.scalars(select(Resource).where(Resource.id.in_(run_resource_ids(run)))).all())
    run_resources = {item.resource_type: item for item in resources}
    order_resource = run_resources.get("order")
    if not order_resource or order_resource.id != resource.id:
        return None, _workflow_command_error("INVALID_RESOURCE", "当前发单资源不属于该运行")

    now = beijing_now()
    if retrying:
        step.retry_count += 1
    transition_step(step, "running")
    step.progress = 0
    step.started_at = now
    step.finished_at = None
    step.duration_ms = None
    step.error_message = None
    transition_run(run, "running")
    run.error_code = None
    run.error_message = None
    append_log(
        db,
        run,
        "workflow.step_retried" if retrying else "workflow.step_started",
        f"{step.name}{'重试' if retrying else '开始'}，正在准备终端发单命令",
        step=step,
        source="terminal",
        detail={"retry_count": step.retry_count, "mode": "terminal"},
    )
    db.flush()
    try:
        summary = await prepare_order_node(
            db, workflow, node, run_resources, run=run, step=step
        )
        command = str(summary.get("generated_command") or "").strip()
        if not command:
            raise WorkflowError("ORDER_COMMAND_EMPTY", "发单命令为空", 409)
    except Exception as exc:
        failed_at = beijing_now()
        message = getattr(exc, "message", str(exc))
        code = getattr(exc, "code", "ORDER_PREPARATION_FAILED")
        transition_step(step, "failed")
        step.progress = 0
        step.error_message = message
        step.finished_at = failed_at
        step.duration_ms = _duration_ms(step.started_at, failed_at)
        transition_run(run, "awaiting_step_retry")
        run.error_code = None
        run.error_message = None
        run.finished_at = None
        append_log(
            db,
            run,
            "workflow.step_failed",
            message,
            level="ERROR",
            step=step,
            source="terminal",
            detail={"error_code": code, "mode": "terminal"},
        )
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
        return None, _workflow_command_error(code, message)

    dispatched_at = beijing_now()
    transition_step(step, "waiting")
    step.progress = 100
    step.finished_at = None
    step.duration_ms = _duration_ms(step.started_at, dispatched_at)
    step.error_message = None
    step.result_summary = {
        **summary,
        "mode": "terminal",
        "resource_id": resource.id,
        "resource_name": resource.name,
        "command": command,
        "exit_code": None,
        "dispatched_by": actor_id,
        "dispatched_at": dispatched_at.isoformat(),
        "process_started": True,
    }
    transition_run(run, "awaiting_step_completion")
    run.progress = int((step.position - 1) * 100 / max(1, len(run.steps)))
    append_log(
        db,
        run,
        "workflow.step_executed",
        f"{step.name}发单命令已在终端下发，等待手动完成",
        step=step,
        source="terminal",
        detail={"command": command, "resource_id": resource.id, "mode": "terminal"},
        log_type="remote_command",
    )
    db.commit()
    broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    return command, {
        "type": "workflow_command",
        "status": "dispatched",
        "command": command,
        "run_id": run.id,
        "step_id": step.id,
        "resource_id": resource.id,
    }


async def _dispatch_workflow_step_command(
    *,
    actor_id: int,
    resource: TerminalResource,
    connection: asyncssh.SSHClientConnection,
    run_id: object,
    step_id: object,
    operation: object,
    lease: typing.Optional[ParserTerminalLease] = None,
) -> typing.Tuple[typing.Union[str, None], dict]:
    db = SessionLocal()
    try:
        run, step, retrying, error = _validate_terminal_step(
            db,
            resource=resource,
            run_id=run_id,
            step_id=step_id,
            operation=operation,
        )
        if error or not run or not step:
            return None, error or _workflow_command_error("INVALID_WORKFLOW_STEP", "运行步骤无效")
        handler = workflow_handler_registry.find(step.node_type)
        terminal_kind = handler.terminal_kind if handler else None
        if terminal_kind == "slnic" and step.node_type == "slnic_merge_capture":
            return await _dispatch_slnic_merge_terminal_command(
                db=db,
                actor_id=actor_id,
                resource=resource,
                run=run,
                step=step,
                retrying=retrying,
            )
        if terminal_kind in {"rem", "slnic"}:
            return _dispatch_shell_terminal_command(
                db=db,
                actor_id=actor_id,
                resource=resource,
                run=run,
                step=step,
                retrying=retrying,
            )
        if terminal_kind == "market":
            return await _dispatch_market_terminal_command(
                db=db,
                actor_id=actor_id,
                resource=resource,
                connection=connection,
                run=run,
                step=step,
                retrying=retrying,
            )
        if terminal_kind == "order":
            return await _dispatch_order_preparation_command(
                db=db,
                actor_id=actor_id,
                resource=resource,
                run=run,
                step=step,
                retrying=retrying,
            )
        if terminal_kind == "parser":
            if lease is None:
                return None, _workflow_command_error("PARSER_TERMINAL_REQUIRED", "解析节点需要绑定 SSH 终端")
            return await _dispatch_parser_terminal_command(
                db=db,
                actor_id=actor_id,
                resource=resource,
                run=run,
                step=step,
                retrying=retrying,
                connection=connection,
                lease=lease,
            )
        return None, _workflow_command_error("INVALID_WORKFLOW_STEP", "当前节点不支持终端执行")
    finally:
        db.close()


def _record_workflow_command_dispatch_failure(
    *,
    resource: TerminalResource,
    run_id: object,
    step_id: object,
    actor_id: int,
) -> dict:
    response = _workflow_command_error("SSH_COMMAND_DISPATCH_FAILED", "SSH 终端命令下发失败，请重试")
    if not isinstance(run_id, int) or not isinstance(step_id, int):
        return response
    db = SessionLocal()
    try:
        run = db.scalar(
            select(TestRun)
            .where(TestRun.id == run_id)
            .options(selectinload(TestRun.steps))
        )
        if not run or run.status != "awaiting_step_completion":
            return response
        step = next((item for item in run.steps if item.id == step_id), None)
        if not step or step.status != "waiting" or resource.id not in run_resource_ids(run):
            return response
        failed_at = beijing_now()
        transition_step(step, "failed")
        step.progress = 0
        step.error_message = response["message"]
        step.finished_at = failed_at
        if step.started_at:
            step.duration_ms = max(0, int((failed_at - step.started_at).total_seconds() * 1000))
        step.result_summary = {
            **(step.result_summary or {}),
            "dispatch_error": response["code"],
        }
        transition_run(
            run,
            "awaiting_step_retry",
            source="terminal",
            actor_id=actor_id,
            reason=response["code"],
        )
        run.error_code = None
        run.error_message = None
        run.finished_at = None
        append_log(
            db,
            run,
            "workflow.step_failed",
            response["message"],
            level="ERROR",
            step=step,
            source="terminal",
            detail={"error_code": response["code"], "resource_id": resource.id},
        )
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
        return response
    finally:
        db.close()


def _record_parser_terminal_disconnect(lease: ParserTerminalLease, resource: TerminalResource) -> None:
    if lease.run_id is None or lease.step_id is None:
        return
    db = SessionLocal()
    try:
        run = db.get(TestRun, lease.run_id, options=[selectinload(TestRun.steps), selectinload(TestRun.resource_links)])
        if not run or resource.id not in run_resource_ids(run) or run.status != "awaiting_step_completion":
            return
        step = next((item for item in run.steps if item.id == lease.step_id), None)
        if not step or step.node_type != "parser_parse" or step.status != "waiting":
            return
        failed_at = beijing_now()
        transition_step(step, "failed")
        step.progress = 0
        step.error_message = "解析 SSH 终端已断开，节点需要重试"
        step.finished_at = failed_at
        step.duration_ms = _duration_ms(step.started_at, failed_at)
        transition_run(run, "awaiting_step_retry")
        run.finished_at = None
        append_log(
            db,
            run,
            "workflow.step_failed",
            step.error_message,
            level="ERROR",
            step=step,
            source="terminal",
            detail={"error_code": "PARSER_TERMINAL_DISCONNECTED", "mode": "terminal"},
        )
        db.commit()
        broker.publish(run.id, {"type": "status", "status": run.status, "progress": run.progress})
    finally:
        db.close()


async def _receive_remote(
    websocket: WebSocket,
    process: asyncssh.SSHClientProcess,
    connection: asyncssh.SSHClientConnection,
    *,
    actor_id: int,
    resource: TerminalResource,
    lease: ParserTerminalLease,
) -> str:
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnected"
        message_type = message.get("type")
        if message_type == "input":
            data = message.get("data")
            if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_INPUT_SIZE:
                await _send(websocket, {"type": "error", "code": "INPUT_TOO_LARGE", "message": "单次输入不能超过 64 KiB"})
                continue
            process.stdin.write(data)
        elif message_type == "resize":
            columns = _clamp(message.get("cols"), MIN_COLUMNS, MAX_COLUMNS, 120)
            rows = _clamp(message.get("rows"), MIN_ROWS, MAX_ROWS, 32)
            process.change_terminal_size(columns, rows)
        elif message_type == "workflow_step_command":
            command, response = await _dispatch_workflow_step_command(
                actor_id=actor_id,
                resource=resource,
                connection=connection,
                run_id=message.get("run_id"),
                step_id=message.get("step_id"),
                operation=message.get("operation"),
                lease=lease,
            )
            if command:
                try:
                    process.stdin.write(f"{command}\r")
                except Exception:
                    response = _record_workflow_command_dispatch_failure(
                        resource=resource,
                        run_id=message.get("run_id"),
                        step_id=message.get("step_id"),
                        actor_id=actor_id,
                    )
            await _send(websocket, response)
        elif message_type == "parser_action":
            db = SessionLocal()
            try:
                response = await _dispatch_parser_action(
                    db=db,
                    actor_id=actor_id,
                    resource=resource,
                    run_id=message.get("run_id"),
                    step_id=message.get("step_id"),
                    action=message.get("action"),
                    lease=lease,
                )
                if response.get("status") == "dispatched":
                    process.stdin.write(f"{response['action']}\r")
                    _record_parser_action_dispatch(
                        db=db,
                        actor_id=actor_id,
                        resource=resource,
                        run_id=typing.cast(int, response["run_id"]),
                        step_id=typing.cast(int, response["step_id"]),
                        action=typing.cast(str, response["action"]),
                    )
            except Exception:
                response = _record_workflow_command_dispatch_failure(
                    resource=resource,
                    run_id=message.get("run_id"),
                    step_id=message.get("step_id"),
                    actor_id=actor_id,
                )
                response["type"] = "parser_action"
            finally:
                db.close()
            await _send(websocket, response)
        else:
            await _send(websocket, {"type": "error", "code": "INVALID_MESSAGE", "message": "不支持的终端消息"})


async def _send_remote_output(websocket: WebSocket, process: asyncssh.SSHClientProcess) -> str:
    while True:
        data = await process.stdout.read(32768)
        if not data:
            await process.wait()
            await _send(websocket, {"type": "exit", "exit_code": process.exit_status})
            return "shell_exit"
        if not await _send(websocket, {"type": "output", "data": data}):
            return "client_disconnected"


async def _run_remote(
    websocket: WebSocket,
    resource: TerminalResource,
    actor_id: int,
    on_connected: typing.Callable[[], None],
    lease: ParserTerminalLease,
) -> str:
    options: typing.Dict[str, object] = {
        "host": resource.host,
        "port": resource.port,
        "username": resource.username,
        "known_hosts": None,
        "connect_timeout": 15,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
    }
    if resource.password:
        options["password"] = resource.password
    if resource.private_key:
        options["client_keys"] = [asyncssh.import_private_key(resource.private_key)]

    connection = await asyncssh.connect(**options)
    process = None
    try:
        process = await connection.create_process(
            _remote_command(resource),
            term_type="xterm-256color",
            term_size=(120, 32),
            encoding="utf-8",
            errors="replace",
        )
        await _send(websocket, {"type": "status", "status": "connected", "message": "SSH 已连接"})
        on_connected()
        receiver = asyncio.create_task(
            _receive_remote(
                websocket,
                process,
                connection,
                actor_id=actor_id,
                resource=resource,
                lease=lease,
            )
        )
        sender = asyncio.create_task(_send_remote_output(websocket, process))
        done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        reason = next(iter(done)).result()
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        return reason
    finally:
        if process is not None:
            process.close()
            with suppress(Exception):
                await process.wait_closed()
        connection.close()
        with suppress(Exception):
            await connection.wait_closed()


async def handle_resource_terminal(websocket: WebSocket, resource_id: int, token: str) -> None:
    trace_token = trace_id_ctx.set(trace_id_ctx.get() or str(uuid4()))
    started_at = beijing_now()
    actor_id: typing.Union[int, None] = None
    resource: typing.Union[TerminalResource, None] = None
    parser_lease = ParserTerminalLease()
    opened = False
    reason = "connection_failed"
    try:
        try:
            context = _load_context(token, resource_id)
        except CredentialSecretError as exc:
            await websocket.accept()
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "RESOURCE_CREDENTIAL_INVALID",
                    "message": exc.message,
                },
            )
            await _close(websocket, 4512)
            return
        if context[0] is None:
            message = context[1]
            close_code = 4401 if "凭据" in message else 4403
            await _close(websocket, close_code)
            return
        actor_id, resource = context
        user_id_ctx.set(actor_id)
        websocket.state.observability_user_id = actor_id
        await websocket.accept()
        await _send(websocket, {"type": "status", "status": "connecting", "message": "正在建立终端会话"})

        def record_open() -> None:
            nonlocal opened
            opened = True
            _audit(websocket, actor_id, resource.id, "resource.terminal.open")

        try:
            reason = await _run_remote(websocket, resource, actor_id, record_open, parser_lease)
        except Exception as exc:
            if not opened:
                _audit(
                    websocket,
                    actor_id,
                    resource.id,
                    "resource.terminal.open",
                    result="failed",
                    detail={"error_type": type(exc).__name__},
                )
            else:
                reason = "session_error"
            code = "SSH_SESSION_FAILED" if opened else "SSH_CONNECTION_FAILED"
            label = "SSH 会话异常" if opened else "SSH 连接失败"
            await _send(websocket, {"type": "error", "code": code, "message": f"{label}：{exc}"})
            await _close(websocket, 4511)
            return

        await _send(websocket, {"type": "status", "status": "closed", "message": "终端会话已结束"})
        await _close(websocket)
    finally:
        if resource is not None and resource.resource_type == "parser":
            _record_parser_terminal_disconnect(parser_lease, resource)
        if opened and actor_id is not None and resource is not None:
            duration_ms = max(0, int((beijing_now() - started_at).total_seconds() * 1000))
            _audit(
                websocket,
                actor_id,
                resource.id,
                "resource.terminal.close",
                detail={"duration_ms": duration_ms, "reason": reason},
            )
        trace_id_ctx.reset(trace_token)


def _load_order_terminal_context(
    token: str,
    run_id: int,
    step_id: int,
) -> typing.Union[typing.Tuple[int, TerminalResource, str], typing.Tuple[None, str]]:
    try:
        payload = decode_token(token, "access")
        actor_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        return None, "登录凭据无效或已过期"
    db = SessionLocal()
    try:
        actor = db.get(User, actor_id)
        run = db.get(TestRun, run_id, options=[selectinload(TestRun.steps), selectinload(TestRun.resource_links)])
        if not actor or not actor.is_active:
            return None, "登录凭据无效或已过期"
        if actor.role not in {"admin", "tester"}:
            return None, "当前用户无权使用资源操作台"
        if not run:
            return None, "运行不存在"
        step = next((item for item in run.steps if item.id == step_id), None)
        if not step or step.node_type != "order_preparation":
            return None, "发单节点不存在"
        resource = db.scalar(select(Resource).where(
            Resource.id.in_(run_resource_ids(run)),
            Resource.resource_type == "order",
            Resource.is_deleted.is_(False),
            Resource.is_enabled.is_(True),
        ))
        if not resource:
            return None, "运行缺少可用的发单资源"
        session = str((step.result_summary or {}).get("tmux_session") or order_session_name(run.id, step.id))
        return actor.id, TerminalResource(
            id=resource.id,
            name=resource.name,
            resource_type=resource.resource_type,
            host=resource.host,
            port=resource.ssh_port,
            username=resource.username,
            password=decrypt_secret(resource.encrypted_password),
            private_key=decrypt_secret(resource.encrypted_private_key),
            remote_path=resource.remote_path,
        ), session
    finally:
        db.close()


async def _receive_read_only(websocket: WebSocket, process: asyncssh.SSHClientProcess) -> str:
    while True:
        try:
            message = await websocket.receive_json()
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnected"
        if message.get("type") == "resize":
            columns = _clamp(message.get("cols"), MIN_COLUMNS, MAX_COLUMNS, 120)
            rows = _clamp(message.get("rows"), MIN_ROWS, MAX_ROWS, 32)
            process.change_terminal_size(columns, rows)
        else:
            await _send(websocket, {"type": "error", "code": "TERMINAL_READ_ONLY", "message": "工作流发单终端为只读，请使用发单动作按钮"})


async def handle_order_workflow_terminal(
    websocket: WebSocket,
    run_id: int,
    step_id: int,
    token: str,
) -> None:
    context = _load_order_terminal_context(token, run_id, step_id)
    if context[0] is None:
        await _close(websocket, 4403)
        return
    actor_id, resource, session = typing.cast(typing.Tuple[int, TerminalResource, str], context)
    user_id_ctx.set(actor_id)
    websocket.state.observability_user_id = actor_id
    await websocket.accept()
    await _send(websocket, {"type": "status", "status": "connecting", "message": "正在连接发单 tmux 会话"})
    options: typing.Dict[str, object] = {
        "host": resource.host,
        "port": resource.port,
        "username": resource.username,
        "known_hosts": None,
        "connect_timeout": 15,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
    }
    if resource.password:
        options["password"] = resource.password
    if resource.private_key:
        options["client_keys"] = [asyncssh.import_private_key(resource.private_key)]
    connection = None
    process = None
    try:
        connection = await asyncssh.connect(**options)
        exists = await connection.run("tmux has-session -t %s 2>/dev/null" % shlex.quote(session), check=False)
        if exists.exit_status != 0:
            await _send(websocket, {"type": "error", "code": "ORDER_SESSION_NOT_STARTED", "message": "发单会话尚未启动或已经结束"})
            await _close(websocket, 4513)
            return
        captured = await connection.run(
            "tmux capture-pane -p -S -5000 -t %s" % shlex.quote(session),
            check=False,
        )
        await _send(websocket, {"type": "status", "status": "connected", "message": "已连接发单 tmux 会话（只读）"})
        if captured.stdout:
            await _send(websocket, {"type": "output", "data": captured.stdout})
        _audit(websocket, actor_id, resource.id, "resource.order_terminal.open", detail={"run_id": run_id, "step_id": step_id})
        process = await connection.create_process(
            "tmux attach-session -r -t %s" % shlex.quote(session),
            term_type="xterm-256color",
            term_size=(120, 32),
            encoding="utf-8",
            errors="replace",
        )
        receiver = asyncio.create_task(_receive_read_only(websocket, process))
        sender = asyncio.create_task(_send_remote_output(websocket, process))
        done, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        if done:
            next(iter(done)).result()
    except Exception as exc:
        await _send(websocket, {"type": "error", "code": "ORDER_TERMINAL_FAILED", "message": "发单终端连接失败：%s" % exc})
        await _close(websocket, 4511)
    finally:
        if process:
            process.close()
            with suppress(Exception):
                await process.wait_closed()
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()
