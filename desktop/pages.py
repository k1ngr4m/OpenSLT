from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.api_client import ApiClient
from desktop.models import BUSINESS_TEXT, RESOURCE_TEXT, ROLE_TEXT, STATUS_TEXT, TERMINAL_STATUSES, ApiError
from desktop.widgets import FormDialog, button, info, save_bytes, set_table, table, warning
from desktop.workers import Worker


def fmt_time(value: Any) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


class BasePage(QWidget):
    changed = Signal()

    def __init__(self, api: ApiClient, user: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.user = user
        self.pool = QThreadPool.globalInstance()

    @property
    def is_admin(self) -> bool:
        return self.user.get("role") == "admin"

    @property
    def can_operate(self) -> bool:
        return self.user.get("role") in {"admin", "tester"}

    def run(self, function: Callable[[], Any], success: Callable[[Any], None] | None = None, *, error: Callable[[Exception], None] | None = None) -> None:
        worker = Worker(function)
        if success:
            worker.signals.succeeded.connect(success)
        worker.signals.failed.connect(error or self.show_error)
        self.pool.start(worker)

    def show_error(self, exception: Exception) -> None:
        QMessageBox.critical(self, "请求失败", str(exception))

    def heading(self, title: str, subtitle: str = "") -> tuple[QVBoxLayout, QHBoxLayout]:
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        left = QVBoxLayout()
        title_widget = QLabel(title)
        title_widget.setObjectName("pageTitle")
        left.addWidget(title_widget)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("muted")
            left.addWidget(note)
        header.addLayout(left)
        header.addStretch()
        root.addLayout(header)
        return root, header


class LoginPage(QWidget):
    logged_in = Signal(dict)

    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QGroupBox("OpenSLT")
        card.setMinimumWidth(390)
        form = QFormLayout(card)
        self.username = __import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit("admin")
        self.password = __import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit("shengli123")
        self.password.setEchoMode(self.password.EchoMode.Password)
        form.addRow("用户名", self.username)
        form.addRow("密码", self.password)
        self.message = QLabel("本地服务已就绪")
        self.message.setObjectName("muted")
        form.addRow(self.message)
        submit = button("登录", self.login, primary=True)
        form.addRow(submit)
        layout.addWidget(card)

    def login(self) -> None:
        self.message.setText("正在登录…")
        self.run_login()

    def run_login(self) -> None:
        from desktop.workers import Worker

        worker = Worker(self.api.login, self.username.text().strip(), self.password.text())
        worker.signals.succeeded.connect(self.on_success)
        worker.signals.failed.connect(self.on_error)
        QThreadPool.globalInstance().start(worker)

    def on_success(self, user: dict[str, Any]) -> None:
        self.message.setText("登录成功")
        self.logged_in.emit(user)

    def on_error(self, exc: Exception) -> None:
        self.message.setText(str(exc))


class DashboardPage(BasePage):
    def __init__(self, api: ApiClient, user: dict[str, Any], navigate: Callable[[str], None]) -> None:
        super().__init__(api, user)
        root, header = self.heading("工作台", "测速任务和基础资源的实时概览")
        create = button("创建测速运行", lambda: navigate("runs"), primary=True)
        header.addWidget(create)
        self.metrics = QGridLayout()
        root.addLayout(self.metrics)
        self.recent = table(["运行编号", "业务", "场景", "状态", "进度", "创建时间"])
        self.recent.cellDoubleClicked.connect(lambda row, _: navigate(f"run:{self.recent.item(row, 0).data(Qt.ItemDataRole.UserRole)}"))
        root.addWidget(self.recent)
        self.load()

    def load(self) -> None:
        self.run(lambda: (self.api.get("/runs"), self.api.get("/resources")), self.populate)

    def populate(self, payload: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        runs, resources = payload
        active = sum(r.get("status") not in TERMINAL_STATUSES for r in runs)
        completed = sum(r.get("status") == "completed" for r in runs)
        healthy = sum(r.get("health_status") == "healthy" for r in resources)
        while self.metrics.count():
            item = self.metrics.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for column, (label, value) in enumerate((("运行总数", len(runs)), ("执行中/待确认", active), ("已完成", completed), ("健康资源", f"{healthy} / {len(resources)}"))):
            box = QGroupBox(label)
            value_label = QLabel(str(value))
            value_label.setObjectName("metricValue")
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(value_label)
            self.metrics.addWidget(box, 0, column)
        rows = []
        for run in runs[:8]:
            item = [run.get("run_number"), BUSINESS_TEXT.get(run.get("business_code"), run.get("business_code")), run.get("config_snapshot", {}).get("scenario", {}).get("name", ""), STATUS_TEXT.get(run.get("status"), run.get("status")), f"{run.get('progress', 0)}%", fmt_time(run.get("created_at"))]
            rows.append(item)
        set_table(self.recent, rows)
        for index, run in enumerate(runs[:8]):
            self.recent.item(index, 0).setData(Qt.ItemDataRole.UserRole, run.get("id"))


class ResourcesPage(BasePage):
    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__(api, user)
        root, header = self.heading("资源管理", "统一管理 REM、模拟市场、发单、抓包和 Coco 节点")
        if self.is_admin:
            header.addWidget(button("新增资源", self.add_resource, primary=True))
            header.addWidget(button("删除选中", self.delete_selected))
        header.addWidget(button("连通测试", self.health))
        self.rows = table(["名称", "类型", "业务", "连接地址", "健康", "启用", "操作"])
        self.rows.cellDoubleClicked.connect(self.edit_selected)
        root.addWidget(self.rows)
        self.load()

    def load(self) -> None:
        self.run(lambda: self.api.get("/resources"), self.populate)

    def populate(self, resources: list[dict[str, Any]]) -> None:
        set_table(self.rows, [[r["name"], RESOURCE_TEXT.get(r["resource_type"], r["resource_type"]), BUSINESS_TEXT.get(r["business_code"], r["business_code"]), f"{r['username']}@{r['host']}:{r['ssh_port']}", r.get("health_status", "unknown"), "是" if r.get("is_enabled") else "否", "双击编辑"] for r in resources])
        for index, resource in enumerate(resources):
            self.rows.item(index, 0).setData(Qt.ItemDataRole.UserRole, resource)

    def selected(self) -> dict[str, Any] | None:
        row = self.rows.currentRow()
        return self.rows.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def edit_selected(self, *_: Any) -> None:
        resource = self.selected()
        if resource and self.is_admin:
            self.open_resource(resource)

    def add_resource(self) -> None:
        self.open_resource(None)

    def open_resource(self, resource: dict[str, Any] | None) -> None:
        values = resource or {}
        dialog = FormDialog("编辑资源" if resource else "新增资源", self)
        dialog.add_line("name", "名称", values.get("name", ""))
        dialog.add_combo("resource_type", "类型", [(v, k) for k, v in RESOURCE_TEXT.items()], values.get("resource_type", "rem"))
        dialog.add_combo("business_code", "所属业务", [(v, k) for k, v in BUSINESS_TEXT.items()], values.get("business_code", "fut_mm"))
        dialog.add_line("host", "Linux 地址", values.get("host", ""))
        dialog.add_spin("ssh_port", "SSH 端口", values.get("ssh_port", 22), 1, 65535)
        dialog.add_line("username", "用户名", values.get("username", ""))
        dialog.add_combo("auth_type", "认证方式", [("密码", "password"), ("私钥", "private_key")], values.get("auth_type", "password"))
        dialog.add_line("password", "密码", "", password=True)
        dialog.add_text("private_key", "私钥", "")
        dialog.add_line("remote_path", "远程路径", values.get("remote_path", ""))
        dialog.add_text("notes", "备注", values.get("notes", ""))
        dialog.add_check("is_enabled", "启用", values.get("is_enabled", True))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = {key: dialog.value(key) for key in ("name", "resource_type", "business_code", "host", "ssh_port", "username", "auth_type", "password", "private_key", "remote_path", "notes", "is_enabled")}
        payload["capabilities"] = values.get("capabilities", {})
        payload["version_info"] = values.get("version_info", "")
        path = f"/resources/{resource['id']}" if resource else "/resources"
        self.run(lambda: self.api.put(path, json=payload) if resource else self.api.post(path, json=payload), lambda _: self.load())

    def health(self) -> None:
        resource = self.selected()
        if not resource:
            return
        self.run(lambda: self.api.post(f"/resources/{resource['id']}/health"), lambda result: (info(self, "连接测试", result.get("message", "完成")), self.load()))

    def delete_selected(self) -> None:
        resource = self.selected()
        if not resource or not warning(self, "删除资源", f"确定删除资源“{resource['name']}”？"):
            return
        self.run(lambda: self.api.delete(f"/resources/{resource['id']}"), lambda _: self.load())


class PlansPage(BasePage):
    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__(api, user)
        root, header = self.heading("方案与场景", "版本化配置测速流程，历史运行保存独立快照")
        if self.can_operate:
            header.addWidget(button("新增方案", self.add_plan, primary=True))
            header.addWidget(button("新增场景", lambda: self.open_scenario(None)))
            header.addWidget(button("复制选中", self.copy_selected))
        self.plans = table(["方案", "业务", "版本", "描述", "启用"])
        self.plans.cellDoubleClicked.connect(self.edit_plan)
        self.scenarios = table(["场景", "所属方案", "类型", "版本", "所需资源", "启用"])
        self.scenarios.cellDoubleClicked.connect(self.edit_scenario)
        root.addWidget(QLabel("方案（双击编辑）"))
        root.addWidget(self.plans)
        root.addWidget(QLabel("场景（双击编辑）"))
        root.addWidget(self.scenarios)
        self.plan_data: list[dict[str, Any]] = []
        self.scenario_data: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        self.run(lambda: (self.api.get("/plans"), self.api.get("/scenarios")), self.populate)

    def populate(self, payload: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        self.plan_data, self.scenario_data = payload
        names = {p["id"]: p["name"] for p in self.plan_data}
        set_table(self.plans, [[p["name"], BUSINESS_TEXT.get(p["business_code"], p["business_code"]), p.get("config_version"), p.get("description", ""), "是" if p.get("is_enabled") else "否"] for p in self.plan_data])
        set_table(self.scenarios, [[s["name"], names.get(s["plan_id"], str(s["plan_id"])), s.get("scenario_type"), s.get("config_version"), ", ".join(RESOURCE_TEXT.get(t, t) for t in s.get("required_resource_types", [])), "是" if s.get("is_enabled") else "否"] for s in self.scenario_data])
        for i, value in enumerate(self.plan_data): self.plans.item(i, 0).setData(Qt.ItemDataRole.UserRole, value)
        for i, value in enumerate(self.scenario_data): self.scenarios.item(i, 0).setData(Qt.ItemDataRole.UserRole, value)

    def add_plan(self) -> None: self.open_plan(None)
    def edit_plan(self, *_: Any) -> None:
        row = self.plans.currentRow()
        if row >= 0 and self.can_operate: self.open_plan(self.plans.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def open_plan(self, plan: dict[str, Any] | None) -> None:
        values = plan or {}
        dialog = FormDialog("编辑方案" if plan else "新增方案", self)
        dialog.add_line("name", "名称", values.get("name", ""))
        dialog.add_combo("business_code", "业务", [(v, k) for k, v in BUSINESS_TEXT.items()], values.get("business_code", "fut_mm"))
        dialog.add_line("config_version", "配置版本", values.get("config_version", "1.0"))
        dialog.add_text("description", "描述", values.get("description", ""))
        dialog.add_check("is_enabled", "启用", values.get("is_enabled", True))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        payload = {k: dialog.value(k) for k in ("name", "business_code", "config_version", "description", "is_enabled")}
        payload["default_resource_ids"] = values.get("default_resource_ids", [])
        path = f"/plans/{plan['id']}" if plan else "/plans"
        self.run(lambda: self.api.put(path, json=payload) if plan else self.api.post(path, json=payload), lambda _: self.load())

    def edit_scenario(self, *_: Any) -> None:
        row = self.scenarios.currentRow()
        if row >= 0 and self.can_operate: self.open_scenario(self.scenarios.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def open_scenario(self, scenario: dict[str, Any] | None) -> None:
        if not self.plan_data:
            info(self, "无法新增", "请先创建方案。")
            return
        values = scenario or {}
        dialog = FormDialog("编辑场景" if scenario else "新增场景", self)
        dialog.add_combo("plan_id", "所属方案", [(p["name"], str(p["id"])) for p in self.plan_data], str(values.get("plan_id", self.plan_data[0]["id"] if self.plan_data else "")))
        dialog.add_line("name", "场景名称", values.get("name", ""))
        dialog.add_line("scenario_type", "场景类型", values.get("scenario_type", "order"))
        dialog.add_line("config_version", "配置版本", values.get("config_version", "1.0"))
        dialog.add_line("required_resource_types", "所需资源", ",".join(values.get("required_resource_types", [])))
        dialog.add_text("parameters", "参数 JSON", json.dumps(values.get("parameters", {}), ensure_ascii=False, indent=2), json=True)
        dialog.add_text("actions", "动作 JSON", json.dumps(values.get("actions", []), ensure_ascii=False, indent=2), json=True)
        dialog.add_text("statistics_rules", "统计规则 JSON", json.dumps(values.get("statistics_rules", {}), ensure_ascii=False, indent=2), json=True)
        dialog.add_check("is_enabled", "启用", values.get("is_enabled", True))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        try:
            payload = {"plan_id": int(dialog.value("plan_id")), "name": dialog.value("name"), "scenario_type": dialog.value("scenario_type"), "config_version": dialog.value("config_version"), "required_resource_types": [v.strip() for v in dialog.value("required_resource_types").split(",") if v.strip()], "parameters": json.loads(dialog.value("parameters")), "actions": json.loads(dialog.value("actions")), "statistics_rules": json.loads(dialog.value("statistics_rules")), "expected_artifacts": values.get("expected_artifacts", []), "is_enabled": dialog.value("is_enabled")}
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.show_error(ValueError(f"场景 JSON 或字段无效：{exc}")); return
        path = f"/scenarios/{scenario['id']}" if scenario else "/scenarios"
        self.run(lambda: self.api.put(path, json=payload) if scenario else self.api.post(path, json=payload), lambda _: self.load())

    def copy_selected(self) -> None:
        scenario_row = self.scenarios.currentRow()
        if scenario_row >= 0 and self.scenarios.hasFocus():
            scenario = self.scenarios.item(scenario_row, 0).data(Qt.ItemDataRole.UserRole)
            self.run(lambda: self.api.post(f"/scenarios/{scenario['id']}/copy"), lambda _: self.load())
            return
        plan_row = self.plans.currentRow()
        if plan_row >= 0:
            plan = self.plans.item(plan_row, 0).data(Qt.ItemDataRole.UserRole)
            self.run(lambda: self.api.post(f"/plans/{plan['id']}/copy"), lambda _: self.load())


class RunsPage(BasePage):
    open_run = Signal(int)

    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__(api, user)
        root, header = self.heading("测速运行", "创建、排队并跟踪每一次独立测速执行")
        if self.can_operate: header.addWidget(button("创建运行", self.create_run, primary=True))
        self.rows = table(["运行编号", "业务", "方案/场景", "状态", "进度", "创建时间"])
        self.rows.cellDoubleClicked.connect(lambda row, _: self.open_run.emit(self.rows.item(row, 0).data(Qt.ItemDataRole.UserRole)))
        root.addWidget(self.rows)
        self.data: list[dict[str, Any]] = []
        self.plans: list[dict[str, Any]] = []
        self.scenarios: list[dict[str, Any]] = []
        self.resources: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        self.run(lambda: (self.api.get("/runs"), self.api.get("/plans"), self.api.get("/scenarios"), self.api.get("/resources")), self.populate)

    def populate(self, payload: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        self.data, self.plans, self.scenarios, self.resources = payload
        set_table(self.rows, [[r["run_number"], BUSINESS_TEXT.get(r.get("business_code"), r.get("business_code")), f"{r.get('config_snapshot', {}).get('plan', {}).get('name', '')} / {r.get('config_snapshot', {}).get('scenario', {}).get('name', '')}", STATUS_TEXT.get(r.get("status"), r.get("status")), f"{r.get('progress', 0)}%", fmt_time(r.get("created_at"))] for r in self.data])
        for i, value in enumerate(self.data): self.rows.item(i, 0).setData(Qt.ItemDataRole.UserRole, value["id"])

    def create_run(self) -> None:
        if not self.plans or not self.scenarios or not self.resources:
            info(self, "无法创建", "请先配置启用的方案、场景和资源。"); return
        dialog = FormDialog("创建测速运行", self)
        plan_combo = dialog.add_combo("plan_id", "测速方案", [(p["name"], str(p["id"])) for p in self.plans if p.get("is_enabled")])
        scenario_combo = dialog.add_combo("scenario_id", "测速场景", [])
        resource_list = QListWidget()
        resource_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        dialog.form.addRow("执行资源", resource_list)
        dialog.add_spin("timeout_minutes", "超时时间（分钟）", 120, 5, 1440)

        def refresh() -> None:
            plan_id = int(plan_combo.currentData() or 0)
            scenario_combo.clear(); resource_list.clear()
            for s in self.scenarios:
                if s.get("plan_id") == plan_id and s.get("is_enabled"): scenario_combo.addItem(f"{s['name']} · v{s.get('config_version')}", str(s["id"]))
            plan = next((p for p in self.plans if p["id"] == plan_id), None)
            for r in self.resources:
                if r.get("business_code") == (plan or {}).get("business_code") and r.get("is_enabled"):
                    item = QListWidgetItem(f"{r['name']} · {RESOURCE_TEXT.get(r['resource_type'], r['resource_type'])}")
                    item.setData(Qt.ItemDataRole.UserRole, r["id"])
                    resource_list.addItem(item)
        plan_combo.currentIndexChanged.connect(refresh); refresh()
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        selected_ids = [item.data(Qt.ItemDataRole.UserRole) for item in resource_list.selectedItems()]
        if not selected_ids:
            info(self, "无法创建", "至少选择一个执行资源。")
            return
        payload = {"plan_id": int(dialog.value("plan_id")), "scenario_id": int(dialog.value("scenario_id")), "resource_ids": selected_ids, "timeout_minutes": dialog.value("timeout_minutes")}
        self.run(lambda: self.api.post("/runs", json=payload), lambda result: self.open_run.emit(result["id"]))


class RunDetailPage(BasePage):
    back = Signal()

    def __init__(self, api: ApiClient, user: dict[str, Any], run_id: int) -> None:
        super().__init__(api, user)
        self.run_id = run_id
        self.run_data: dict[str, Any] = {}
        self.socket = QWebSocket()
        self.socket.textMessageReceived.connect(self.on_socket_message)
        self.socket.disconnected.connect(self.on_socket_disconnected)
        self.socket.open(QUrl(self.api.websocket_url(run_id)))
        self.root, self.header = self.heading(f"运行 #{run_id}")
        self.back_button = button("← 返回运行列表", self.back.emit)
        self.header.insertWidget(0, self.back_button)
        self.actions = QHBoxLayout(); self.header.addLayout(self.actions)
        self.summary = QLabel("加载中…"); self.summary.setObjectName("summary")
        self.root.addWidget(self.summary)
        self.tabs = QTabWidget(); self.root.addWidget(self.tabs)
        self.timeline = table(["步骤", "状态", "进度", "耗时", "错误"])
        self.logs = table(["时间", "级别", "来源", "消息", "Trace ID"])
        self.metrics = table(["指标", "值", "单位", "样本数", "规则"])
        self.artifacts = table(["文件", "类型", "大小", "SHA-256"])
        self.artifacts.cellDoubleClicked.connect(self.download_artifact)
        self.tabs.addTab(self.timeline, "步骤时间线"); self.tabs.addTab(self.logs, "实时日志"); self.tabs.addTab(self.metrics, "指标与结论"); self.tabs.addTab(self.artifacts, "产物与报告")
        self.timer = QTimer(self); self.timer.timeout.connect(self.load); self.timer.start(5000)
        self.load()

    def on_socket_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except ValueError:
            return
        if payload.get("type") in {"snapshot", "status"}:
            if self.run_data:
                self.run_data["status"] = payload.get("status", self.run_data.get("status"))
                self.run_data["progress"] = payload.get("progress", self.run_data.get("progress", 0))
            QTimer.singleShot(200, self.load)

    def on_socket_disconnected(self) -> None:
        # The five-second timer remains active as the automatic polling fallback.
        pass

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.socket.close()
        super().closeEvent(event)

    def load(self) -> None:
        self.run(lambda: (self.api.get(f"/runs/{self.run_id}"), self.api.get(f"/runs/{self.run_id}/logs")), self.populate)

    def populate(self, payload: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
        self.run_data, logs = payload
        status = self.run_data.get("status", "")
        self.summary.setText(f"状态：{STATUS_TEXT.get(status, status)}    进度：{self.run_data.get('progress', 0)}%    Trace ID：{self.run_data.get('trace_id', '')}")
        while self.actions.count():
            item = self.actions.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if self.can_operate:
            if status in {"draft", "resource_queue"}: self.actions.addWidget(button("启动运行", lambda: self.action("start")))
            if status == "awaiting_wiring": self.actions.addWidget(button("确认接线完成", lambda: self.action("confirm-wiring")))
            if status == "awaiting_review": self.actions.addWidget(button("提交人工结论", self.submit_verdict, primary=True))
            if status in {"resource_queue", "awaiting_wiring", "awaiting_review"}: self.actions.addWidget(button("暂停", lambda: self.action("pause")))
            if status == "paused": self.actions.addWidget(button("恢复", lambda: self.action("resume")))
            if status in {"precheck_failed", "execution_failed", "parse_failed"}: self.actions.addWidget(button("重试", lambda: self.action("retry")))
            if status == "completed": self.actions.addWidget(button("重新生成报告", lambda: self.action("reports")))
            if status not in TERMINAL_STATUSES: self.actions.addWidget(button("取消运行", lambda: self.action("cancel")))
        set_table(self.timeline, [[f"{s.get('position')}. {s.get('name')}", STATUS_TEXT.get(s.get("status"), s.get("status")), f"{s.get('progress', 0)}%", f"{s.get('duration_ms')} ms" if s.get("duration_ms") is not None else "-", s.get("error_message", "")] for s in self.run_data.get("steps", [])])
        set_table(self.logs, [[fmt_time(l.get("created_at")), l.get("level"), l.get("source"), l.get("message"), l.get("trace_id")] for l in logs])
        set_table(self.metrics, [[m.get("name"), m.get("value"), m.get("unit"), m.get("sample_count"), m.get("rule_result") or "-"] for m in self.run_data.get("metrics", [])])
        set_table(self.artifacts, [[a.get("name"), a.get("artifact_type"), f"{a.get('size', 0) / 1024:.1f} KB", a.get("checksum")] for a in self.run_data.get("artifacts", [])])
        for i, artifact in enumerate(self.run_data.get("artifacts", [])):
            self.artifacts.item(i, 0).setData(Qt.ItemDataRole.UserRole, artifact["id"])

    def action(self, name: str) -> None:
        if name == "cancel" and not warning(self, "取消运行", "取消后将执行安全清理并释放资源，确定继续？"): return
        self.run(lambda: self.api.post(f"/runs/{self.run_id}/{name}"), lambda _: self.load())

    def submit_verdict(self) -> None:
        dialog = FormDialog("提交人工复核结论", self)
        dialog.add_combo("final_result", "最终结论", [("通过", "passed"), ("有条件通过", "conditional"), ("不通过", "failed")], "passed")
        dialog.add_text("issue_description", "问题说明")
        dialog.add_text("notes", "备注")
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        payload = {"final_result": dialog.value("final_result"), "issue_description": dialog.value("issue_description"), "notes": dialog.value("notes")}
        self.run(lambda: self.api.post(f"/runs/{self.run_id}/verdict", json=payload), lambda _: self.load())

    def download_artifact(self, row: int, _: int) -> None:
        artifact_id = self.artifacts.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not artifact_id: return
        self.run(lambda: self.api.download_info(f"/artifacts/{artifact_id}/download"), lambda payload: save_bytes(self, payload[0], payload[1]))


class LogsPage(BasePage):
    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__(api, user)
        root, header = self.heading("日志中心", "通过 trace_id 关联 API、运行步骤和远程命令")
        refresh = button("刷新", self.load); header.addWidget(refresh)
        if self.is_admin: header.addWidget(button("导出审计 CSV", self.export_audit))
        self.rows = table(["UTC 时间", "级别", "类型", "来源", "事件", "消息", "Trace ID"])
        root.addWidget(self.rows)
        self.audit = table(["UTC 时间", "操作人", "动作", "对象", "结果", "Trace ID"])
        if self.is_admin:
            root.addWidget(QLabel("审计日志")); root.addWidget(self.audit)
        self.load()

    def load(self) -> None:
        self.run(lambda: (self.api.get("/logs"), self.api.get("/audit-logs") if self.is_admin else []), self.populate)

    def populate(self, payload: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        logs, audits = payload
        set_table(self.rows, [[fmt_time(l.get("created_at")), l.get("level"), l.get("log_type"), l.get("source"), l.get("event"), l.get("message"), l.get("trace_id")] for l in logs])
        if self.is_admin: set_table(self.audit, [[fmt_time(a.get("created_at")), a.get("actor_id"), a.get("action"), f"{a.get('object_type')}#{a.get('object_id')}", a.get("result"), a.get("trace_id")] for a in audits])

    def export_audit(self) -> None:
        self.run(lambda: self.api.download_info("/audit-logs/export"), lambda payload: save_bytes(self, payload[0], payload[1]))


class UsersPage(BasePage):
    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__(api, user)
        root, header = self.heading("用户管理", "管理员创建账号并分配最小必要权限")
        header.addWidget(button("新增用户", self.add_user, primary=True))
        self.rows = table(["用户名", "显示名称", "角色", "状态", "最后登录"])
        self.rows.cellDoubleClicked.connect(self.edit_user)
        root.addWidget(self.rows); self.data: list[dict[str, Any]] = []; self.load()

    def load(self) -> None: self.run(lambda: self.api.get("/users"), self.populate)
    def populate(self, users: list[dict[str, Any]]) -> None:
        self.data = users; set_table(self.rows, [[u.get("username"), u.get("display_name"), ROLE_TEXT.get(u.get("role"), u.get("role")), "启用" if u.get("is_active") else "停用", fmt_time(u.get("last_login_at"))] for u in users])
        for i, value in enumerate(users): self.rows.item(i, 0).setData(Qt.ItemDataRole.UserRole, value)
    def add_user(self) -> None: self.open_user(None)
    def edit_user(self, *_: Any) -> None:
        row = self.rows.currentRow()
        if row >= 0: self.open_user(self.rows.item(row, 0).data(Qt.ItemDataRole.UserRole))
    def open_user(self, user: dict[str, Any] | None) -> None:
        values = user or {}; dialog = FormDialog("编辑用户" if user else "新增用户", self)
        dialog.add_line("username", "用户名", values.get("username", "")); dialog.fields["username"].setEnabled(not bool(user))
        dialog.add_line("display_name", "显示名称", values.get("display_name", "")); dialog.add_line("password", "密码", "", password=True)
        dialog.add_combo("role", "角色", [(v, k) for k, v in ROLE_TEXT.items()], values.get("role", "visitor"))
        if user: dialog.add_check("is_active", "启用", values.get("is_active", True))
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        if user:
            payload = {"display_name": dialog.value("display_name"), "role": dialog.value("role"), "is_active": dialog.value("is_active")}
            if dialog.value("password"): payload["password"] = dialog.value("password")
            self.run(lambda: self.api.patch(f"/users/{user['id']}", json=payload), lambda _: self.load())
        else:
            payload = {"username": dialog.value("username"), "display_name": dialog.value("display_name"), "password": dialog.value("password"), "role": dialog.value("role")}
            self.run(lambda: self.api.post("/users", json=payload), lambda _: self.load())


class MainWindow(QMainWindow):
    logged_out = Signal()

    def __init__(self, api: ApiClient, user: dict[str, Any]) -> None:
        super().__init__()
        self.api = api; self.user = user; self.setWindowTitle("OpenSLT · 自动化测速平台"); self.resize(1280, 820)
        central = QWidget(); layout = QHBoxLayout(central); self.setCentralWidget(central)
        sidebar = QVBoxLayout(); brand = QLabel("OpenSLT\n自动化测速平台"); brand.setObjectName("brand"); sidebar.addWidget(brand)
        self.stack = QStackedWidget(); self.pages: dict[str, QWidget] = {}
        entries = [("dashboard", "工作台"), ("runs", "测速运行"), ("plans", "方案与场景"), ("resources", "资源管理"), ("logs", "日志中心")]
        if user.get("role") == "admin": entries.append(("users", "用户管理"))
        for key, text in entries:
            item = QPushButton(text); item.setObjectName("navButton"); item.clicked.connect(lambda _=False, k=key: self.navigate(k)); sidebar.addWidget(item)
        sidebar.addStretch(); account = QLabel(f"{user.get('display_name') or user.get('username')}\n{ROLE_TEXT.get(user.get('role'), user.get('role'))}"); account.setObjectName("account"); sidebar.addWidget(account)
        logout = button("退出登录", self.logout); sidebar.addWidget(logout)
        layout.addLayout(sidebar, 0); layout.addWidget(self.stack, 1)
        self.add_page("dashboard", DashboardPage(api, user, self.navigate)); self.add_page("runs", RunsPage(api, user)); self.add_page("plans", PlansPage(api, user)); self.add_page("resources", ResourcesPage(api, user)); self.add_page("logs", LogsPage(api, user))
        if user.get("role") == "admin": self.add_page("users", UsersPage(api, user))
        runs_page = self.pages["runs"]; runs_page.open_run.connect(lambda run_id: self.navigate(f"run:{run_id}"))
        self.navigate("dashboard")

    def add_page(self, key: str, page: QWidget) -> None:
        self.pages[key] = page; self.stack.addWidget(page)

    def navigate(self, key: str) -> None:
        current = self.stack.currentWidget()
        if isinstance(current, RunDetailPage) and not key.startswith("run:"):
            current.timer.stop()
            current.socket.close()
            self.stack.removeWidget(current)
            current.deleteLater()
        if key.startswith("run:"):
            run_id = int(key.split(":", 1)[1]); page = RunDetailPage(self.api, self.user, run_id); page.back.connect(lambda: self.navigate("runs")); self.stack.addWidget(page); self.stack.setCurrentWidget(page); return
        if key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])
            loader = getattr(self.pages[key], "load", None)
            if loader: loader()

    def logout(self) -> None:
        try:
            self.api.logout()
        except Exception:
            pass
        finally:
            self.logged_out.emit()
            self.close()
