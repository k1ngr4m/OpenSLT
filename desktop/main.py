from __future__ import annotations

import argparse
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox, QStackedWidget

from desktop.api_client import ApiClient
from desktop.pages import LoginPage, MainWindow
from desktop.runtime import BackendProcess, run_backend


def stylesheet() -> str:
    return """
    QWidget { font-family: 'Microsoft YaHei'; font-size: 13px; color: #20343a; }
    QMainWindow, QStackedWidget { background: #f3f6f8; }
    #brand { color: #5fe0c6; background: #10252c; padding: 22px; font-size: 18px; font-weight: 700; }
    #navButton { text-align: left; color: #d5e5e8; background: #10252c; border: 0; padding: 12px 22px; }
    #navButton:hover, #navButton:checked { background: #18373e; color: #5fe0c6; }
    #account, #muted { color: #71858c; }
    #pageTitle { font-size: 24px; font-weight: 700; color: #10252c; }
    #metricValue { font-size: 28px; font-weight: 700; color: #10252c; }
    #summary { background: white; border: 1px solid #e2e9ec; border-radius: 8px; padding: 14px; margin: 8px 0; }
    QPushButton[primary='true'] { background: #159b83; color: white; border: 0; padding: 8px 16px; border-radius: 5px; }
    QTableWidget { background: white; border: 1px solid #e2e9ec; gridline-color: #edf1f2; }
    QHeaderView::section { background: #edf4f4; padding: 8px; border: 0; font-weight: 600; }
    QGroupBox { background: white; border: 1px solid #e2e9ec; border-radius: 8px; padding: 16px; }
    """


def launch() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--backend", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    args, _ = parser.parse_known_args()
    if args.backend:
        try:
            run_backend(args.port)
            return 0
        except Exception:
            from desktop.runtime import application_root

            log_dir = application_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "backend-startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            return 1

    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    backend = BackendProcess(preferred_port=args.port)
    try:
        backend.start()
    except Exception as exc:
        QMessageBox.critical(None, "OpenSLT 启动失败", str(exc))
        return 1
    api = ApiClient(backend.base_url)
    login = LoginPage(api)
    stack = QStackedWidget(); stack.setWindowTitle("OpenSLT"); stack.resize(1280, 820); stack.addWidget(login); stack.show()
    state: dict[str, MainWindow] = {}

    def on_logged_in(user: dict) -> None:
        window = MainWindow(api, user)
        state["window"] = window
        def show_login() -> None:
            stack.show()
            stack.setCurrentWidget(login)
        window.logged_out.connect(show_login)
        stack.hide()
        window.show()

    login.logged_in.connect(on_logged_in)
    result = app.exec()
    api.close(); backend.stop()
    return result


if __name__ == "__main__":
    raise SystemExit(launch())
