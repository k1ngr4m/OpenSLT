import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop.pages import MainWindow


class FakeApi:
    def get(self, path, **kwargs):
        if path.startswith("/runs/") and not path.endswith("/logs"):
            return {"id": 1, "status": "draft", "progress": 0, "steps": [], "metrics": [], "artifacts": []}
        return []

    def post(self, path, **kwargs):
        return {}

    def put(self, path, **kwargs):
        return {}

    def patch(self, path, **kwargs):
        return {}

    def delete(self, path):
        return None

    def logout(self):
        return None

    def websocket_url(self, run_id):
        return f"ws://127.0.0.1:1/api/v1/ws/runs/{run_id}?token=test"


def test_admin_window_contains_all_navigation_pages() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(FakeApi(), {"username": "admin", "display_name": "管理员", "role": "admin"})
    assert set(window.pages) == {"dashboard", "runs", "plans", "resources", "logs", "users"}
    window.navigate("run:1")
    assert window.stack.currentWidget().run_id == 1
    window.close()
    app.processEvents()


def test_visitor_window_hides_user_management() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(FakeApi(), {"username": "guest", "display_name": "访客", "role": "visitor"})
    assert "users" not in window.pages
    window.close()
    app.processEvents()
