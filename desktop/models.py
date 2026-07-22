from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    trace_id: str | None = None
    status_code: int | None = None
    details: Any = None

    def __str__(self) -> str:
        suffix = f"\nTrace ID: {self.trace_id}" if self.trace_id else ""
        return f"{self.message}{suffix}"


STATUS_TEXT = {
    "draft": "草稿",
    "resource_queue": "资源排队",
    "prechecking": "预检查",
    "awaiting_wiring": "等待接线确认",
    "executing": "执行中",
    "parsing": "解析中",
    "awaiting_review": "等待人工复核",
    "completed": "已完成",
    "paused": "已暂停",
    "cancelled": "已取消",
    "precheck_failed": "预检查失败",
    "execution_failed": "执行失败",
    "parse_failed": "解析失败",
    "timed_out": "已超时",
    "pending": "等待中",
    "running": "执行中",
    "succeeded": "成功",
    "failed": "失败",
    "skipped": "已跳过",
}

BUSINESS_TEXT = {
    "fut_mm": "软核做市",
    "rem_two": "整合版二期",
    "rem_two_mm": "整合版二期做市",
}

RESOURCE_TEXT = {
    "rem": "REM",
    "market": "模拟市场",
    "order": "发单客户端",
    "capture": "抓包节点",
    "coco": "Coco 节点",
}

ROLE_TEXT = {"admin": "管理员", "tester": "测试人员", "visitor": "访客"}

TERMINAL_STATUSES = {
    "completed",
    "cancelled",
    "execution_failed",
    "parse_failed",
    "precheck_failed",
    "timed_out",
}
