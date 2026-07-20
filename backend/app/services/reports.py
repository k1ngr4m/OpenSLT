import hashlib
from pathlib import Path

from jinja2 import Template
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Artifact, TestRun


REPORT_TEMPLATE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>body{font-family:sans-serif;color:#1f2937;margin:32px}h1{color:#0f766e}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d1d5db;padding:8px;text-align:left}.muted{color:#64748b}</style></head><body>
<h1>OpenSLT 测速报告</h1><p class="muted">运行编号：{{ run.run_number }}</p>
<table><tr><th>业务</th><td>{{ run.business_code }}</td><th>状态</th><td>{{ run.status }}</td></tr>
<tr><th>开始时间</th><td>{{ run.started_at or '-' }}</td><th>结束时间</th><td>{{ run.finished_at or '-' }}</td></tr></table>
<h2>步骤时间线</h2><table><tr><th>步骤</th><th>状态</th><th>耗时(ms)</th></tr>{% for step in run.steps %}<tr><td>{{ step.name }}</td><td>{{ step.status }}</td><td>{{ step.duration_ms or '-' }}</td></tr>{% endfor %}</table>
<h2>统计指标</h2><table><tr><th>指标</th><th>值</th><th>单位</th><th>规则</th></tr>{% for metric in run.metrics %}<tr><td>{{ metric.name }}</td><td>{{ '%.3f'|format(metric.value) }}</td><td>{{ metric.unit }}</td><td>{{ metric.rule_result or '-' }}</td></tr>{% endfor %}</table>
<h2>最终结论</h2><p>{{ run.verdict.final_result if run.verdict and run.verdict.final_result else '待复核' }}</p><p>{{ run.verdict.issue_description if run.verdict else '' }}</p><p>{{ run.verdict.notes if run.verdict else '' }}</p>
</body></html>"""


def _register(db: Session, run: TestRun, path: Path, artifact_type: str, content_type: str) -> Artifact:
    data = path.read_bytes()
    existing = next((a for a in run.artifacts if a.artifact_type == artifact_type), None)
    artifact = existing or Artifact(run_id=run.id, artifact_type=artifact_type, name=path.name, path=str(path))
    artifact.name = path.name
    artifact.path = str(path)
    artifact.content_type = content_type
    artifact.size = len(data)
    artifact.checksum = hashlib.sha256(data).hexdigest()
    if not existing:
        db.add(artifact)
    db.flush()
    return artifact


def generate_reports(db: Session, run: TestRun) -> list[Artifact]:
    directory = settings.artifact_root / run.business_code / str(run.plan_id) / str(run.scenario_id) / run.run_number / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    html = Template(REPORT_TEMPLATE).render(run=run)
    html_path = directory / "report.html"
    html_path.write_text(html, encoding="utf-8")

    workbook = Workbook()
    summary = workbook.active
    summary.title = "运行摘要"
    summary.append(["运行编号", run.run_number])
    summary.append(["业务", run.business_code])
    summary.append(["状态", run.status])
    metrics = workbook.create_sheet("指标")
    metrics.append(["指标", "值", "单位", "规则结果", "样本数"])
    for metric in run.metrics:
        metrics.append([metric.name, metric.value, metric.unit, metric.rule_result, metric.sample_count])
    steps = workbook.create_sheet("步骤")
    steps.append(["顺序", "步骤", "状态", "耗时(ms)", "错误"])
    for step in run.steps:
        steps.append([step.position, step.name, step.status, step.duration_ms, step.error_message])
    xlsx_path = directory / "report.xlsx"
    workbook.save(xlsx_path)

    pdf_path = directory / "report.pdf"
    try:
        from weasyprint import HTML
        HTML(string=html, base_url=str(directory)).write_pdf(pdf_path)
    except Exception:
        # A valid minimal PDF fallback keeps reporting available when native WeasyPrint libs are absent.
        content = b"BT /F1 12 Tf 72 720 Td (OpenSLT report: " + run.run_number.encode("ascii", "ignore") + b") Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf)); pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(pdf); pdf.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]: pdf.extend(f"{offset:010d} 00000 n \n".encode())
        pdf.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
        pdf_path.write_bytes(pdf)

    artifacts = [
        _register(db, run, html_path, "web_report", "text/html; charset=utf-8"),
        _register(db, run, xlsx_path, "excel_report", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        _register(db, run, pdf_path, "pdf_report", "application/pdf"),
    ]
    db.commit()
    return artifacts

