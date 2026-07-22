from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:
            exc.__notes__ = [traceback.format_exc()]
            self.signals.failed.emit(exc)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
