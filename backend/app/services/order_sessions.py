from __future__ import annotations

import shlex
import typing
from contextlib import suppress

import asyncssh

from app.core.security import decrypt_secret
from app.models import Resource, RunStep, TestRun
from app.services.workflows import WorkflowError
from app.workflow_node_configs import ORDER_ACTIONS


def order_session_name(run_id: int, step_id: int) -> str:
    return "openslt-order-r%s-s%s" % (run_id, step_id)


def supported_order_actions(resource: Resource) -> typing.Tuple[str, ...]:
    configured = (resource.capabilities or {}).get("order_actions")
    if not isinstance(configured, list):
        return ORDER_ACTIONS
    actions = tuple(str(item) for item in configured if str(item) in ORDER_ACTIONS)
    return actions or ORDER_ACTIONS


def ssh_options(resource: Resource) -> typing.Dict[str, object]:
    options: typing.Dict[str, object] = {
        "host": resource.host,
        "port": resource.ssh_port,
        "username": resource.username,
        "known_hosts": None,
        "connect_timeout": 15,
        "keepalive_interval": 30,
        "keepalive_count_max": 3,
    }
    password = decrypt_secret(resource.encrypted_password)
    private_key = decrypt_secret(resource.encrypted_private_key)
    if password:
        options["password"] = password
    if private_key:
        options["client_keys"] = [asyncssh.import_private_key(private_key)]
    return options


async def _run(connection: typing.Any, command: str, code: str, message: str) -> typing.Any:
    result = await connection.run(command, check=False)
    if result.exit_status != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise WorkflowError(code, "%s%s" % (message, "：" + detail if detail else ""), 409)
    return result


async def launch_order_session(
    resource: Resource,
    run: TestRun,
    step: RunStep,
    generated_command: str,
    *,
    replace: bool = False,
) -> dict:
    session = order_session_name(run.id, step.id)
    connection = None
    try:
        connection = await asyncssh.connect(**ssh_options(resource))
        await _run(connection, "command -v tmux >/dev/null 2>&1", "ORDER_TMUX_REQUIRED", "发单服务器未安装 tmux")
        exists = await connection.run("tmux has-session -t %s 2>/dev/null" % shlex.quote(session), check=False)
        if exists.exit_status == 0 and replace:
            await connection.run("tmux kill-session -t %s" % shlex.quote(session), check=False)
            exists = await connection.run("tmux has-session -t %s 2>/dev/null" % shlex.quote(session), check=False)
        if exists.exit_status != 0:
            shell_command = "/bin/sh -lc %s" % shlex.quote(generated_command)
            launch = "tmux new-session -d -s %s %s" % (
                shlex.quote(session),
                shlex.quote(shell_command),
            )
            await _run(connection, launch, "ORDER_SESSION_START_FAILED", "启动发单 tmux 会话失败")
        return {
            "tmux_session": session,
            "session_status": "running",
            "process_started": True,
            "order_action_status": "pending",
        }
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("ORDER_SESSION_START_FAILED", "启动发单会话失败：%s" % exc, 409) from exc
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def order_session_exists(resource: Resource, session: str) -> bool:
    connection = None
    try:
        connection = await asyncssh.connect(**ssh_options(resource))
        result = await connection.run("tmux has-session -t %s 2>/dev/null" % shlex.quote(session), check=False)
        return result.exit_status == 0
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def send_order_action(resource: Resource, session: str, action: str) -> None:
    if action not in supported_order_actions(resource):
        raise WorkflowError("ORDER_ACTION_UNSUPPORTED", "发单资源不支持动作 %s" % action, 409)
    connection = None
    try:
        connection = await asyncssh.connect(**ssh_options(resource))
        if not await order_session_exists_on_connection(connection, session):
            raise WorkflowError("ORDER_SESSION_LOST", "发单 tmux 会话不存在，请重试节点", 409)
        literal = "tmux send-keys -t %s -l %s" % (shlex.quote(session), shlex.quote(action))
        await _run(connection, literal, "ORDER_ACTION_FAILED", "发送发单动作失败")
        await _run(
            connection,
            "tmux send-keys -t %s Enter" % shlex.quote(session),
            "ORDER_ACTION_FAILED",
            "发送发单动作回车失败",
        )
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("ORDER_ACTION_FAILED", "发送发单动作失败：%s" % exc, 409) from exc
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def order_session_exists_on_connection(connection: typing.Any, session: str) -> bool:
    result = await connection.run("tmux has-session -t %s 2>/dev/null" % shlex.quote(session), check=False)
    return result.exit_status == 0


async def cleanup_order_session(resource: Resource, session: str) -> bool:
    connection = None
    try:
        connection = await asyncssh.connect(**ssh_options(resource))
        if not await order_session_exists_on_connection(connection, session):
            return False
        await connection.run("tmux send-keys -t %s C-c 2>/dev/null || true" % shlex.quote(session), check=False)
        await _run(
            connection,
            "tmux kill-session -t %s" % shlex.quote(session),
            "ORDER_SESSION_CLEANUP_FAILED",
            "关闭发单 tmux 会话失败",
        )
        return True
    except WorkflowError:
        raise
    except Exception as exc:
        raise WorkflowError("ORDER_SESSION_CLEANUP_FAILED", "关闭发单会话失败：%s" % exc, 409) from exc
    finally:
        if connection:
            connection.close()
            with suppress(Exception):
                await connection.wait_closed()


async def cleanup_order_step_by_ids(run_id: int, step_id: int) -> None:
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import TestRun
    from app.services.resource_relations import run_resource_ids

    db = SessionLocal()
    try:
        run = db.get(TestRun, run_id)
        step = db.get(RunStep, step_id)
        if not run or not step or step.run_id != run.id:
            return
        resource = db.scalar(
            select(Resource).where(
                Resource.id.in_(run_resource_ids(run)),
                Resource.resource_type == "order",
            )
        )
        if not resource:
            return
        summary = dict(step.result_summary or {})
        session = str(summary.get("tmux_session") or order_session_name(run.id, step.id))
        try:
            await cleanup_order_session(resource, session)
            summary["session_status"] = "closed"
        except Exception as exc:
            summary["session_status"] = "cleanup_failed"
            summary["session_error"] = str(exc)
        step.result_summary = summary
        db.commit()
    finally:
        db.close()
