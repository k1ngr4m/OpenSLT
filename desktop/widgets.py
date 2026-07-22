from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def button(text: str, callback=None, *, primary: bool = False) -> QPushButton:
    item = QPushButton(text)
    if primary:
        item.setProperty("primary", True)
    if callback:
        item.clicked.connect(callback)
    return item


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.verticalHeader().setVisible(False)
    return widget


def set_table(widget: QTableWidget, rows: list[list[Any]]) -> None:
    widget.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setData(Qt.ItemDataRole.UserRole, value)
            widget.setItem(row_index, column, item)
    widget.resizeColumnsToContents()


class FormDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.form = QFormLayout()
        self.body = QWidget()
        self.body.setLayout(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.body)
        layout.addWidget(buttons)
        self.fields: dict[str, QWidget] = {}

    def add_line(self, key: str, label: str, value: str = "", *, password: bool = False) -> QLineEdit:
        field = QLineEdit(value)
        if password:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.form.addRow(label, field)
        self.fields[key] = field
        return field

    def add_combo(self, key: str, label: str, values: list[tuple[str, str]], current: str = "") -> QComboBox:
        field = QComboBox()
        for text, value in values:
            field.addItem(text, value)
        if current:
            index = field.findData(current)
            if index >= 0:
                field.setCurrentIndex(index)
        self.form.addRow(label, field)
        self.fields[key] = field
        return field

    def add_multi_combo(self, key: str, label: str, values: list[tuple[str, str]]) -> QComboBox:
        # Qt's built-in combo is intentionally kept simple; comma-separated values are accepted.
        return self.add_combo(key, label, [(", ".join(v for _, v in values), "")])

    def add_spin(self, key: str, label: str, value: int = 0, minimum: int = 0, maximum: int = 100000) -> QSpinBox:
        field = QSpinBox()
        field.setRange(minimum, maximum)
        field.setValue(value)
        self.form.addRow(label, field)
        self.fields[key] = field
        return field

    def add_check(self, key: str, label: str, value: bool = True) -> QCheckBox:
        field = QCheckBox()
        field.setChecked(value)
        self.form.addRow(label, field)
        self.fields[key] = field
        return field

    def add_text(self, key: str, label: str, value: str = "", *, json: bool = False) -> QTextEdit:
        field = QPlainTextEdit(value) if json else QTextEdit(value)
        field.setMinimumHeight(72)
        self.form.addRow(label, field)
        self.fields[key] = field
        return field

    def value(self, key: str) -> Any:
        field = self.fields[key]
        if isinstance(field, QLineEdit):
            return field.text()
        if isinstance(field, QComboBox):
            return field.currentData()
        if isinstance(field, QSpinBox):
            return field.value()
        if isinstance(field, QCheckBox):
            return field.isChecked()
        if isinstance(field, (QTextEdit, QPlainTextEdit)):
            return field.toPlainText()
        return None


def info(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def warning(parent: QWidget, title: str, message: str) -> bool:
    return QMessageBox.warning(parent, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def save_bytes(parent: QWidget, data: bytes, filename: str) -> None:
    path, _ = QFileDialog.getSaveFileName(parent, "保存文件", filename)
    if path:
        with open(path, "wb") as output:
            output.write(data)
        info(parent, "下载完成", f"文件已保存到：\n{path}")
