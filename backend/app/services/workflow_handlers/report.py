from __future__ import annotations

from app.services.reports import generate_reports
from app.services.workflow_handlers.base import WorkflowExecutionContext


class ReportGenerationHandler:
    node_types = ("report_generation",)
    terminal_kind = None

    async def execute(self, context: WorkflowExecutionContext) -> dict:
        result = generate_reports(context.db, context.run, step=context.step, reason="workflow_node")
        context.append_log(
            context.db,
            context.run,
            "report.completed",
            f"报告 v{result['report_version']:03d} 已生成",
            step=context.step,
            source="report",
            detail={
                "report_version": result["report_version"],
                "artifact_ids": result["artifact_ids"],
                "missing_sections": result["missing_sections"],
            },
        )
        return result


HANDLERS = (ReportGenerationHandler(),)
