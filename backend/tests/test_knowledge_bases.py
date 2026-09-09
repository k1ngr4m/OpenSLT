import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import ChatConversation, DurableTask, KnowledgeBase, KnowledgeUpload, ModelProvider, SvnKnowledgeSource
from app.services import knowledge_bases as kb, svn_knowledge as svn
from app.services.durable_tasks import execute_task
from app.services.embedding import EmbeddingClient


@pytest.fixture(autouse=True)
def knowledge_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_root", tmp_path)
    monkeypatch.setattr(EmbeddingClient, "embed", lambda self, texts: [[1.0] + [0.0] * (self.expected_dimensions - 1) for _ in texts])


def create_base(client, headers, name="需求库", dimensions=2):
    provider = client.post('/api/v1/model-providers', headers=headers, json=dict(name=name, kind='embedding', base_url='https://example.com/v1', embedding_dimensions=dimensions)).json()
    model = client.post('/api/v1/model-providers/%s/models' % provider['id'], headers=headers, json=dict(kind='embedding', model_id='embed-' + str(dimensions))).json()
    response = client.post('/api/v1/knowledge-bases', headers=headers, json=dict(name=name, embedding_model_id=model['id']))
    assert response.status_code == 201, response.text
    return response.json(), provider, model


def finish(response, expected="succeeded"):
    assert response.status_code == 202, response.text
    task_id = response.json()['task_id']
    from app.core.time import beijing_now
    for _ in range(3):
        asyncio.run(execute_task(task_id))
        with SessionLocal() as db:
            task = db.get(DurableTask, task_id)
            if task.status != 'queued':
                assert task.status == expected, task.last_error
                break
            task.available_at = beijing_now()
            db.commit()
    else:
        pytest.fail('索引任务未结束')


def upload(client, headers, base_id, name='REQ-123.md', text='需求：账户权限校验'):
    return client.post('/api/v1/knowledge-bases/%s/documents' % base_id, headers=headers, files=[('files', (name, text.encode(), 'text/plain'))])


def test_two_bases_models_search_consumers_and_reference_guards(client, admin_headers):
    first, provider, model = create_base(client, admin_headers)
    second, _, _ = create_base(client, admin_headers, '交易库', 3)
    for base, text in [(first, '账户权限知识'), (second, '交易规则知识')]:
        finish(upload(client, admin_headers, base['id'], text=text))
    for base, expected in [(first, '账户权限知识'), (second, '交易规则知识')]:
        response = client.post('/api/v1/knowledge-bases/%s/search' % base['id'], headers=admin_headers, json={'query': '知识'})
        assert response.status_code == 200, response.text
        assert [item['snippet'] for item in response.json()['results']] == [expected]
        overview = client.get('/api/v1/knowledge-bases/%s' % base['id'], headers=admin_headers).json()
        assert overview['document_count'] == 1 and overview['chunk_count'] == 1
        requirements = client.get('/api/v1/smart-cases/requirements', headers=admin_headers, params={'knowledge_base_id': base['id']})
        assert len(requirements.json()) == 1, requirements.text
    assert client.post('/api/v1/smart-cases/knowledge-search', headers=admin_headers, json={'query': '知识'}).status_code == 409
    assert client.post('/api/v1/chat/conversations', headers=admin_headers, json={'mode': 'knowledge'}).status_code == 409
    response = client.post('/api/v1/chat/conversations', headers=admin_headers, json={'mode': 'knowledge', 'knowledge_base_id': first['id']})
    assert response.json()['knowledge_base_id'] == first['id']
    assert client.delete('/api/v1/model-providers/%s' % provider['id'], headers=admin_headers).status_code == 409
    assert client.delete('/api/v1/model-providers/models/%s' % model['id'], headers=admin_headers).status_code == 409
    assert client.get('/api/v1/knowledge-bases/99999', headers=admin_headers).status_code == 404


def test_upload_validation_isolation_delete_and_empty_index(client, admin_headers):
    base, _, _ = create_base(client, admin_headers)
    path = '/api/v1/knowledge-bases/%s' % base['id']
    assert upload(client, admin_headers, base['id'], '../evil.md').status_code == 422
    assert upload(client, admin_headers, base['id'], 'evil.exe').status_code == 422
    assert upload(client, admin_headers, base['id'], text='').status_code == 422
    pending = upload(client, admin_headers, base['id'])
    assert upload(client, admin_headers, base['id'], 'other.md').status_code == 409
    finish(pending)
    assert upload(client, admin_headers, base['id']).status_code == 409
    doc = client.get(path + '/documents', headers=admin_headers).json()[0]
    second, _, _ = create_base(client, admin_headers, '第二个')
    assert client.delete('/api/v1/knowledge-bases/%s/documents/%s' % (second['id'], doc['upload_id']), headers=admin_headers).status_code == 404
    finish(client.delete(path + '/documents/%s' % doc['upload_id'], headers=admin_headers))
    assert client.get(path + '/documents', headers=admin_headers).json() == []
    result = client.post(path + '/search', headers=admin_headers, json={'query': '知识'})
    assert result.status_code == 200 and result.json()['results'] == []


def test_upload_compares_complete_long_names_without_a_prefix_index(client, admin_headers):
    base, _, _ = create_base(client, admin_headers)
    names = ['需' * 251 + suffix + '.md' for suffix in ('甲', '乙')]
    for name in names:
        finish(upload(client, admin_headers, base['id'], name=name))
    for name in names:
        assert upload(client, admin_headers, base['id'], name=name).status_code == 409
    assert upload(client, admin_headers, base['id'], name='需' * 253 + '.md').status_code == 422
    docs = client.get('/api/v1/knowledge-bases/%s/documents' % base['id'], headers=admin_headers).json()
    assert {doc['name'] for doc in docs} == set(names)
    other, _, _ = create_base(client, admin_headers, '另一库')
    finish(upload(client, admin_headers, other['id'], name=names[0]))


def test_failure_cancel_retry_and_chunk_settings(client, admin_headers, monkeypatch):
    base, provider, _ = create_base(client, admin_headers)
    path = '/api/v1/knowledge-bases/%s' % base['id']
    finish(upload(client, admin_headers, base['id']))
    original = kb.index_path(base['id']).read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(EmbeddingClient, 'embed', lambda *args: (_ for _ in ()).throw(RuntimeError('embedding failed')))
        finish(upload(client, admin_headers, base['id'], 'REQ-456.md'), 'failed')
    assert kb.index_path(base['id']).read_bytes() == original
    pending = client.post(path + '/index', headers=admin_headers)
    assert client.post(path + '/index/cancel', headers=admin_headers).json()['status'] == 'cancelled'
    assert kb.index_path(base['id']).read_bytes() == original
    finish(client.post(path + '/index', headers=admin_headers))
    assert len(client.get(path + '/documents', headers=admin_headers).json()) == 2
    assert client.put(path, headers=admin_headers, json=dict(name='新名称', chunk_size=20, chunk_overlap=20)).status_code == 422
    assert client.put(path, headers=admin_headers, json=dict(name='新名称', chunk_size=20, chunk_overlap=2, top_k=1)).status_code == 200
    assert client.post(path + '/search', headers=admin_headers, json={'query': '知识'}).status_code == 409
    finish(client.post(path + '/index', headers=admin_headers))
    assert len(client.post(path + '/search', headers=admin_headers, json={'query': '知识'}).json()['results']) == 1
    # API key rotation keeps valid vectors; dimension changes invalidate only referenced bases.
    second, _, _ = create_base(client, admin_headers, '不受影响')
    finish(upload(client, admin_headers, second['id']))
    for dims, expected in [(2, 'succeeded'), (3, 'stale')]:
        response = client.put('/api/v1/model-providers/%s' % provider['id'], headers=admin_headers, json=dict(name=provider['name'], base_url=provider['base_url'], api_key='rotated', embedding_dimensions=dims))
        assert response.status_code == 200, response.text
        assert client.get(path, headers=admin_headers).json()['index_status'] == expected
        assert client.get('/api/v1/knowledge-bases/%s' % second['id'], headers=admin_headers).json()['index_status'] == 'succeeded'


def test_svn_and_upload_merge_without_overwriting(client, admin_headers, tmp_path, monkeypatch):
    base, _, _ = create_base(client, admin_headers)
    path = '/api/v1/knowledge-bases/%s' % base['id']
    root = tmp_path / 'fixture-svn'
    root.mkdir()
    (root / 'REQ-456.md').write_text('SVN 文档正文')
    seen = []
    def sync(client, repository, relative, username, password, base_id):
        seen.append(base_id)
        return root, '123'
    monkeypatch.setattr(svn, '_sync_working_copy', sync)
    monkeypatch.setattr(svn.SvnClient, 'version', lambda self: '1.14')
    response = client.put(path + '/svn', headers=admin_headers, json=dict(repository_urls=['https://svn.example/project'], username='reader', password='secret', include_paths=['docs']))
    assert response.status_code == 200, response.text
    finish(upload(client, admin_headers, base['id']))
    assert seen == [base['id']]
    docs = client.get(path + '/documents', headers=admin_headers).json()
    assert {doc['origin'] for doc in docs} == {'svn', 'upload'}
    (root / 'REQ-456.md').write_text('更新 SVN 文档')
    finish(client.post(path + '/index', headers=admin_headers))
    docs = client.get(path + '/documents', headers=admin_headers).json()
    assert len(docs) == 2
    finish(upload(client, admin_headers, base['id'], '第三份.md'))
    assert len(seen) == 2  # Upload changes do not contact SVN.
    assert len(client.get(path + '/documents', headers=admin_headers).json()) == 3


def test_bad_document_reports_error_and_permissions(client, admin_headers):
    base, _, _ = create_base(client, admin_headers)
    finish(upload(client, admin_headers, base['id']))
    finish(upload(client, admin_headers, base['id'], 'bad.pdf', 'not a PDF'))
    doc = next(doc for doc in client.get('/api/v1/knowledge-bases/%s/documents' % base['id'], headers=admin_headers).json() if doc['name'] == 'bad.pdf')
    assert doc['status'] == 'failed' and doc['error']
    for role, allowed in [('tester', True), ('visitor', False)]:
        created = client.post('/api/v1/users', headers=admin_headers, json={'username': role, 'display_name': role, 'password': 'Password123!', 'role': role})
        assert created.status_code == 201, created.text
        token = client.post('/api/v1/auth/login', json={'username': role, 'password': 'Password123!'}).json()['access_token']
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/v1/knowledge-bases', headers=headers).status_code == (200 if allowed else 403)
        assert client.post('/api/v1/knowledge-bases', headers=headers, json=dict(name='禁止创建', embedding_model_id=base['embedding_model_id'])).status_code == 403


def test_first_failed_file_is_visible_and_can_be_deleted(client, admin_headers):
    base, _, _ = create_base(client, admin_headers)
    finish(upload(client, admin_headers, base['id'], 'bad.pdf', 'invalid PDF'), 'failed')
    path = '/api/v1/knowledge-bases/%s' % base['id']
    doc = client.get(path + '/documents', headers=admin_headers).json()[0]
    assert doc['status'] == 'failed' and doc['error']
    finish(client.delete(path + '/documents/%s' % doc['upload_id'], headers=admin_headers))
    assert client.get(path + '/documents', headers=admin_headers).json() == []


def test_chat_and_generation_use_selected_library(client, admin_headers, monkeypatch):
    from app.services.llm import LlmClient
    from app.services import smart_case_generation
    from app.models import SmartCaseGeneration
    first, _, _ = create_base(client, admin_headers)
    second, _, _ = create_base(client, admin_headers, '交易知识', 3)
    finish(upload(client, admin_headers, first['id'], text='第一库账户规则'))
    finish(upload(client, admin_headers, second['id'], text='第二库交易规则'))
    provider = client.post('/api/v1/model-providers', headers=admin_headers, json=dict(name='对话', kind='chat', base_url='https://example.com/v1')).json()
    model = client.post('/api/v1/model-providers/%s/models' % provider['id'], headers=admin_headers, json=dict(kind='chat', model_id='chat-model')).json()
    client.post('/api/v1/model-providers/models/%s/activate' % model['id'], headers=admin_headers)
    async def embed_async(self, texts):
        return self.embed(texts)
    received = []
    async def stream(self, messages, *args):
        received.extend(messages)
        yield '答案 [1]'
    monkeypatch.setattr(EmbeddingClient, 'embed_async', embed_async)
    monkeypatch.setattr(LlmClient, 'stream', stream)
    conversation = client.post('/api/v1/chat/conversations', headers=admin_headers, json=dict(mode='knowledge', knowledge_base_id=second['id'])).json()
    response = client.post('/api/v1/chat/conversations/%s/messages' % conversation['id'], headers=admin_headers, json={'content': '规则是什么？'})
    assert response.status_code == 200, response.text
    evidence = str(received)
    assert '第二库交易规则' in evidence and '第一库账户规则' not in evidence
    assert '第二库交易规则' in response.text
    source = client.get('/api/v1/smart-cases/requirements', headers=admin_headers, params={'knowledge_base_id': first['id']}).json()[0]
    references = []
    def generate(llm, requirement, docs, **kwargs):
        references.extend(docs)
        return [dict(title='账户校验', preconditions=[], steps=['检查账户'], expected_results=['符合规则'], case_type='功能', priority='高')]
    monkeypatch.setattr(smart_case_generation, 'generate_cases', generate)
    response = client.post('/api/v1/smart-cases/generations', headers=admin_headers, json=dict(knowledge_base_id=first['id'], requirement_path=source['source_path']))
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        task = db.scalar(select(DurableTask).where(DurableTask.task_type == 'smart_case_generate'))
        task_id = task.id
    asyncio.run(execute_task(task_id))
    with SessionLocal() as db:
        generation = db.get(SmartCaseGeneration, response.json()['id'])
        assert generation.status == 'succeeded', generation.error
        assert generation.knowledge_base_id == first['id']
    assert '第一库账户规则' in str(references) and '第二库交易规则' not in str(references)


def test_legacy_files_are_copied_and_missing_model_can_be_bound_once(client, admin_headers, monkeypatch):
    from app.models import AiModel
    base, _, _ = create_base(client, admin_headers)
    finish(upload(client, admin_headers, base['id']))
    old = settings.knowledge_root / 'published' / 'svn-index.sqlite3'
    old.parent.mkdir()
    old.write_bytes(kb.index_path(base['id']).read_bytes())
    kb.index_path(base['id']).unlink()
    with SessionLocal() as db:
        item = db.get(KnowledgeBase, base['id'])
        item.legacy_index = True
        db.add(SvnKnowledgeSource(knowledge_base_id=item.id, repository_url='https://svn.example', repository_urls=['https://svn.example'], username='reader', encrypted_password='encrypted', include_paths=['docs']))
        db.commit()
    monkeypatch.setattr(svn, 'published_index_matches', lambda *args: True)
    kb.migrate_legacy_indexes()
    assert kb.index_path(base['id']).read_bytes() == old.read_bytes()
    assert old.exists()
    kb.migrate_legacy_indexes()
    with SessionLocal() as db:
        assert db.get(KnowledgeBase, base['id']).index_status == 'succeeded'
        db.add(KnowledgeBase(name='待绑定迁移库'))
        db.commit()
        missing = db.scalar(select(KnowledgeBase).where(KnowledgeBase.embedding_model_id.is_(None)))
        missing_id = missing.id
    path = '/api/v1/knowledge-bases/%s' % missing_id
    response = client.put(path, headers=admin_headers, json=dict(name='补选模型', embedding_model_id=base['embedding_model_id']))
    assert response.status_code == 200
    second, _, _ = create_base(client, admin_headers, '另一个模型', 3)
    assert client.put(path, headers=admin_headers, json=dict(name='禁止换模型', embedding_model_id=second['embedding_model_id'])).status_code == 409


def test_interrupted_index_resumes_through_existing_durable_recovery(client, admin_headers):
    from datetime import timedelta
    from app.core.time import beijing_now
    from app.services.durable_tasks import recover_abandoned_tasks
    base, _, _ = create_base(client, admin_headers)
    response = upload(client, admin_headers, base['id'])
    with SessionLocal() as db:
        task = db.get(DurableTask, response.json()['task_id'])
        task.status, task.attempts, task.locked_by = 'running', 1, 'stopped-worker'
        task.lease_expires_at = beijing_now() - timedelta(seconds=1)
        db.commit()
        assert recover_abandoned_tasks(db) == 1
    finish(response)
    with SessionLocal() as db:
        assert kb.index_ready(db, db.get(KnowledgeBase, base['id']))
