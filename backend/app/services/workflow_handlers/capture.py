from __future__ import annotations

from app.services import workflows
from app.services.workflow_handlers.base import WorkflowExecutionContext
from app.wiring_profiles import refresh_pending_wiring_snapshots
from app.workflow_node_configs import WiringConfirmationConfig, parse_node_config


class WiringConfirmationHandler:
    node_types = ("wiring_confirmation",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        config = parse_node_config(context.node.node_type, context.node.config or {})
        assert isinstance(config, WiringConfirmationConfig)
        return {
            "diagram": config.diagram,
            "confirmed": False,
        }


class ServerCaptureHandler:
    node_types = ("server_config",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        snapshots = await workflows.capture_server(
            context.db,
            context.scenario,
            context.workflow,
            context.node,
            scope="run",
            actor_id=context.run.created_by,
            run_id=context.run.id,
            run_step_id=context.step.id,
            run_resources=context.resources,
        )
        summary = _capture_summary(context, snapshots, "服务器配置采集不完整")
        rem_resource = context.resources.get("rem")
        if rem_resource:
            rem_snapshot = next(
                (
                    snapshot
                    for snapshot in snapshots
                    if snapshot.resource_id == rem_resource.id
                    and snapshot.status == "succeeded"
                ),
                None,
            )
            ip_item = next(
                (
                    item
                    for item in (rem_snapshot.items if rem_snapshot else [])
                    if item.item_key == "ip" and item.status == "succeeded"
                ),
                None,
            )
            if ip_item and ip_item.value_text:
                refresh_pending_wiring_snapshots(
                    context.run,
                    context.step,
                    ip_item.value_text,
                )
        return summary


class DatabaseCaptureHandler:
    node_types = ("database_config",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        snapshots = await workflows.capture_database(
            context.db,
            context.scenario,
            context.workflow,
            context.node,
            scope="run",
            actor_id=context.run.created_by,
            run_id=context.run.id,
            run_step_id=context.step.id,
            run_resources=context.resources,
        )
        return _capture_summary(context, snapshots, "数据库配置采集不完整")


def _capture_summary(context: WorkflowExecutionContext, snapshots: list, failure_message: str) -> dict:
    failed = [item for item in snapshots if item.status == "failed"]
    summary = {
        "snapshot_ids": [item.id for item in snapshots],
        "sources": len(snapshots),
        "failed": len(failed),
    }
    if failed:
        context.step.result_summary = summary
        raise workflows.WorkflowError("CONFIG_CAPTURE_FAILED", failure_message, 409)
    return summary


HANDLERS = (WiringConfirmationHandler(), ServerCaptureHandler(), DatabaseCaptureHandler())
