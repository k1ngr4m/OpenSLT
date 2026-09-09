import asyncio
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import DurableTask, SmartCaseGeneration, User
from app.services.durable_tasks import _execute_payload
from app.services.llm import LlmError, parse_cases, revise_cases
from test_account_models import _configure, _tester


CASES = parse_cases(json.dumps([
    {'title': '登录成功', 'steps': ['输入账号', '提交'], 'expected_results': ['账号可见', '进入首页']},
    {'title': '登录失败', 'steps': ['输入错误密码'], 'expected_results': ['提示错误']},
]))


def test_revision_preserves_unselected_content_and_rejects_invalid_patches():
    def complete(messages):
        payload = json.loads(messages[1]['content'])
        assert payload['instruction'] == '名称更简洁'
        assert payload['editable_fields'] == ['title']
        assert payload['cases'] == [{'index': 1, **CASES[1]}]
        return json.dumps({'cases': [{'index': 1, 'title': '拒绝错误密码', 'steps': ['不允许写入']}]})

    result = revise_cases(SimpleNamespace(complete=complete), CASES, [1], ['title'], '名称更简洁', '登录')
    assert result == [CASES[0], {**CASES[1], 'title': '拒绝错误密码'}]
    assert CASES[1]['title'] == '登录失败'
    for raw, fields in [
        ('not json', ['title']),
        ('{"cases":[]}', ['title']),
        ('{"cases":[{"index":0,"title":"错误序号"}]}', ['title']),
        ('{"cases":[{"index":true,"title":"错误类型"}]}', ['title']),
        ('{"cases":[{"index":1}]}', ['title']),
        ('{"cases":[{"index":1,"title":"登录成功"}]}', ['title']),
        ('{"cases":[{"index":1,"steps":["第一步","第二步"]}]}', ['steps']),
        ('{"cases":[{"index":1,"steps":[null]}]}', ['steps']),
        ('{"cases":[{"index":1,"priority":"紧急"}]}', ['priority']),
    ]:
        with pytest.raises(LlmError):
            revise_cases(SimpleNamespace(complete=lambda _: raw), CASES, [1], fields, '修改', '登录')
    raw = json.dumps({'cases': [{'index': 1, 'steps': ['输入', '提交'], 'expected_results': ['输入可见', '提示错误']}]})
    updated = revise_cases(SimpleNamespace(complete=lambda _: raw), CASES, [1], ['steps', 'expected_results'], '拆分步骤', '登录')
    assert updated[1]['steps'] == ['输入', '提交']
    assert updated[0] == CASES[0]


def test_revision_task_is_private_preserves_original_exports_and_can_be_revised_again(client, admin_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'artifact_root', tmp_path)
    _configure(client, admin_headers)
    _, other_headers = _tester(client, admin_headers)
    with SessionLocal() as db:
        original = SmartCaseGeneration(
            requirement_path='需求/登录.md', requirement_revision='1', requirement_name='登录',
            llm_model='old-model', status='succeeded', case_count=2, result_cases=CASES,
            created_by=db.scalar(select(User.id).where(User.username == 'admin')),
            referenced_sources=[{'source_path': '需求/登录.md', 'revision': '1'}],
        )
        db.add(original)
        db.commit()
        source_id = original.id
    url = f'/api/v1/smart-cases/generations/{source_id}/revise'
    payload = {'case_indices': [1], 'fields': ['title'], 'instruction': '  名称更清晰  '}
    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, headers=other_headers, json=payload).status_code == 404
    for invalid in ({'case_indices': []}, {'case_indices': [2]}, {'case_indices': [-1]},
                    {'case_indices': [True]}, {'fields': []}, {'fields': ['unknown']},
                    {'instruction': ' '}, {'instruction': '字' * 4001}):
        assert client.post(url, headers=admin_headers, json={**payload, **invalid}).status_code == 422
    response = client.post(url, headers=admin_headers, json=payload)
    assert response.status_code == 202, response.text
    new_id = response.json()['id']
    assert new_id != source_id
    monkeypatch.setattr('app.services.llm.LlmClient.complete', lambda self, messages: json.dumps({'cases': [{'index': 1, 'title': '错误密码无法登录'}]}))
    with SessionLocal() as db:
        task = db.scalar(select(DurableTask).where(DurableTask.task_type == 'smart_case_generate'))
        assert task.payload['revision']['instruction'] == '名称更清晰'
        assert task.payload['revision']['source_generation_id'] == source_id
        # No knowledge source or embedding is needed to refine existing cases.
        asyncio.run(_execute_payload(task))
        asyncio.run(_execute_payload(task))  # Completed task replay is harmless.
    result_url = f'/api/v1/smart-cases/generations/{new_id}'
    revised = client.get(result_url, headers=admin_headers).json()
    assert revised['result_cases'] == [CASES[0], {**CASES[1], 'title': '错误密码无法登录'}]
    assert revised['referenced_sources'] == [{'source_path': '需求/登录.md', 'revision': '1'}]
    workbook = load_workbook(BytesIO(client.get(result_url + '/download', headers=admin_headers).content))
    assert workbook.active['D3'].value == '错误密码无法登录'
    assert workbook.active['D2'].value == '登录成功'
    workbook.close()
    followup = client.post(result_url + '/revise', headers=admin_headers, json=payload)
    assert followup.status_code == 202
    followup_id = followup.json()['id']
    assert client.post(f'/api/v1/smart-cases/generations/{followup_id}/revise', headers=admin_headers, json=payload).status_code == 409
    monkeypatch.setattr('app.services.llm.LlmClient.complete', lambda *args: 'invalid')
    with SessionLocal() as db:
        task = db.scalar(select(DurableTask).order_by(DurableTask.id.desc()))
        with pytest.raises(LlmError):
            asyncio.run(_execute_payload(task))
        assert db.get(SmartCaseGeneration, followup_id).status == 'failed'
    assert client.get(result_url, headers=admin_headers).json()['result_cases'] == revised['result_cases']
    assert client.get(f'/api/v1/smart-cases/generations/{source_id}', headers=admin_headers).json()['result_cases'] == CASES
