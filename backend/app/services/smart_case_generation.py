from __future__ import annotations

import hashlib
import json
import os
import typing
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import redact
from app.core.security import decrypt_secret
from app.core.time import beijing_now
from app.models import CaseGenerationPrompt, AiModel, ModelProvider, SmartCaseGeneration, SvnKnowledgeSource
from app.services.embedding import EmbeddingClient
from app.services.llm import LlmClient, generate_cases, revise_cases
from app.services.model_providers import require_active_model
from app.services.svn_knowledge import get_indexed_document, published_index_matches, search_vector_index


def _safe_excel(value: typing.Any) -> str:
    text = str(value or "")
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def build_workbook(path: Path, generation: SmartCaseGeneration, cases: typing.Sequence[typing.Mapping[str, typing.Any]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试用例"
    headers = ["用例编号", "需求编号", "需求名称", "用例名称", "前置条件", "测试步骤", "预期结果", "用例类型", "优先级", "状态", "来源", "备注"]
    sheet.append(headers)
    for index, case in enumerate(cases, 1):
        sheet.append([
            "TC-%04d" % index,
            generation.requirement_no or "",
            generation.requirement_name,
            case["title"],
            "\n".join(case["preconditions"]),
            "\n".join("%d. %s" % (i, value) for i, value in enumerate(case["steps"], 1)),
            "\n".join("%d. %s" % (i, value) for i, value in enumerate(case["expected_results"], 1)),
            case["case_type"], case["priority"], "草稿待复核", generation.requirement_path, "",
        ])
    fill = PatternFill("solid", fgColor="14545A")
    for cell in sheet[1]:
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.value = _safe_excel(cell.value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    widths = [14, 18, 24, 34, 28, 48, 48, 12, 10, 14, 42, 24]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    notes = workbook.create_sheet("生成说明")
    notes.append(["项目", "内容"])
    notes.append(["生成模型", generation.llm_model])
    notes.append(["生成时间", beijing_now().isoformat()])
    notes.append(["选中需求", generation.requirement_path])
    notes.append(["需求 revision", generation.requirement_revision])
    notes.append(["复核要求", "本文件为 AI 生成草稿，执行前必须由测试人员复核。"])
    for item in generation.referenced_sources:
        notes.append(["参考来源", "%s（r%s）" % (item["source_path"], item["revision"])])
    for cell in notes[1]:
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
    notes.column_dimensions["A"].width = 18
    notes.column_dimensions["B"].width = 90
    notes.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        workbook.save(temporary)
        os.replace(str(temporary), str(path))
        path.chmod(0o600)
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)


from app.services import knowledge_bases as kb


def execute_smart_case_generation(generation_id: int, additional_prompt: str = "", revision: typing.Optional[typing.Dict[str, typing.Any]] = None) -> None:
    db = SessionLocal()
    try:
        generation = db.get(SmartCaseGeneration, generation_id)
        if generation is None:
            raise RuntimeError("智能用例生成任务不存在")
        if generation.status == "succeeded":
            return
        if generation.encrypted_llm_config:
            llm = LlmClient(**json.loads(decrypt_secret(generation.encrypted_llm_config)))
        else:
            chat_model = db.get(AiModel, generation.ai_model_id) if generation.ai_model_id else None
            if chat_model is None or chat_model.kind != "chat":
                chat_provider, chat_model = require_active_model(db, "chat", generation.created_by)
            else:
                chat_provider = db.get(ModelProvider, chat_model.provider_id)
                if chat_provider is None:
                    raise RuntimeError("用例生成任务使用的模型提供商不存在")
            llm = LlmClient(chat_provider.base_url, chat_model.model_id, decrypt_secret(chat_provider.encrypted_api_key))
        generation.status = "running"
        generation.error = None
        db.commit()
        if revision:
            original = db.get(SmartCaseGeneration, revision["source_generation_id"])
            if original is None or original.created_by != generation.created_by or original.status != "succeeded":
                raise RuntimeError("待修改的用例记录不可用")
            cases = revise_cases(llm, original.result_cases, revision["case_indices"], revision["fields"],
                                 revision["instruction"], generation.requirement_name)
        else:
            base = kb.resolve_base(db, generation.knowledge_base_id)
            source = kb.source_for(db, base.id) if base else db.scalar(select(SvnKnowledgeSource).order_by(SvnKnowledgeSource.id).limit(1))
            if base is None and source is None:
                raise RuntimeError("知识源不存在")
            embedding_provider, embedding_model = kb.embedding_model(db, base) if base else require_active_model(db, "embedding")
            ready = kb.index_ready(db, base) if base else published_index_matches(source, embedding_provider.base_url, embedding_model.model_id, embedding_provider.embedding_dimensions)
            revisions = kb.index_revisions(base.id) if base else dict(source.last_revisions)
            if not ready or dict(generation.index_revisions) != revisions:
                raise RuntimeError("知识索引已变化，请重新选择需求")
            selected = get_indexed_document(generation.requirement_path, kb.index_path(base.id)) if base else get_indexed_document(generation.requirement_path)
            if selected["revision"] != generation.requirement_revision:
                raise RuntimeError("需求 revision 已变化，请重新选择需求")
            query = "%s %s %s" % (generation.requirement_no or "", generation.requirement_name, selected["content"][:1500])
            embedding = EmbeddingClient(
                embedding_provider.base_url,
                embedding_model.model_id,
                decrypt_secret(embedding_provider.encrypted_api_key),
                expected_dimensions=embedding_provider.embedding_dimensions,
            )
            vector = embedding.embed([query])[0]
            hits = search_vector_index(query, vector, base.top_k, index_path=kb.index_path(base.id)) if base else search_vector_index(query, vector, 8)
            references = [{"source_path": selected["source_path"], "revision": selected["revision"], "content": selected["content"][:12000]}]
            seen = {selected["source_path"]}
            for hit in hits:
                if hit["source_path"] in seen:
                    continue
                seen.add(hit["source_path"])
                references.append({"source_path": hit["source_path"], "revision": hit["revision"], "content": hit["snippet"][:1500]})
            generation.referenced_sources = [{"source_path": item["source_path"], "revision": item["revision"]} for item in references]
            if base and kb.index_revisions(base.id) != dict(generation.index_revisions):
                raise RuntimeError("知识索引已变化，请重新选择需求")
            prompt_config = db.get(CaseGenerationPrompt, 1)
            cases = generate_cases(
                llm,
                {"requirement_no": generation.requirement_no or "", "requirement_name": generation.requirement_name, "source_path": generation.requirement_path, "revision": generation.requirement_revision},
                references,
                additional_prompt=additional_prompt,
                **({"system_prompt": prompt_config.system_prompt, "user_prompt": prompt_config.user_prompt} if prompt_config else {}),
            )
        path = settings.artifact_root / "smart-cases" / ("generation-%s.xlsx" % generation.id)
        build_workbook(path, generation, cases)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        generation.result_cases = list(cases)
        generation.case_count = len(cases)
        generation.artifact_path = str(path)
        generation.artifact_size = path.stat().st_size
        generation.artifact_checksum = digest
        generation.status = "succeeded"
        db.commit()
    except Exception as exc:
        db.rollback()
        generation = db.get(SmartCaseGeneration, generation_id)
        if generation:
            generation.status = "failed"
            generation.error = str(redact(str(exc)))[:1000]
            db.commit()
        raise
    finally:
        db.close()
