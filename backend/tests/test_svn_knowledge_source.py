from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import redact
from app.models import AuditLog, DurableTask, SmartCaseGeneration, SvnKnowledgeSource
from app.services.llm import parse_cases
from app.services.smart_case_generation import build_workbook
from app.services.svn_knowledge import (
    SvnClient,
    _build_manifest,
    _publish_vector_index,
    _svn_targets,
    _xml_root,
    enqueue_due_svn_syncs,
    list_indexed_requirements,
    normalize_include_paths,
    normalize_repository_urls,
    search_vector_index,
    test_svn_connection as check_svn_connection,
)
from app.core.time import beijing_now


def _payload(**overrides):
    value = {
        "repository_urls": ["http://svn.intranet.example/svn/knowledge"],
        "username": "openslt-readonly",
        "password": "svn-secret-value",
        "include_paths": ["docs/测试文档", "docs/需求文档"],
        "sync_interval_minutes": 30,
        "enabled": True,
        "allow_insecure_http": True,
    }
    value.update(overrides)
    return value


def _add_model(client, headers, kind, base_url, model_id, api_key=None):
    provider = client.post(
        "/api/v1/model-providers",
        headers=headers,
        json={
            "name": "%s-provider" % kind,
            "base_url": base_url,
            "api_key": api_key,
            "allow_insecure_http": base_url.startswith("http://"),
        },
    ).json()
    model = client.post(
        "/api/v1/model-providers/%s/models" % provider["id"],
        headers=headers,
        json={"kind": kind, "model_id": model_id},
    ).json()
    assert client.post(
        "/api/v1/model-providers/models/%s/activate" % model["id"], headers=headers
    ).status_code == 200
    return model


def _configure_models(client, headers):
    embedding = _add_model(
        client, headers, "embedding", "http://embedding.intranet.example/v1", "bge-m3"
    )
    chat = _add_model(client, headers, "chat", "http://llm.intranet.example/v1", "qwen3")
    return embedding, chat


def test_svn_config_validates_scope_and_never_returns_or_queues_password(client, admin_headers) -> None:
    assert redact({"embedding_api_key": "secret"}) == {"embedding_api_key": "[REDACTED]"}
    refused = client.put(
        "/api/v1/smart-cases/knowledge-source",
        headers=admin_headers,
        json=_payload(allow_insecure_http=False),
    )
    assert refused.status_code == 422
    assert normalize_include_paths(["docs/tests", "docs/tests"]) == ["docs/tests"]
    assert normalize_repository_urls(
        ["https://svn.example/one/", "https://svn.example/two"], False,
    ) == ["https://svn.example/one", "https://svn.example/two"]
    assert [item[2] for item in _svn_targets(
        ["https://svn.example/one", "https://svn.example/two"], ["docs/tests"],
    )] == ["https://svn.example/one/docs/tests", "https://svn.example/two/docs/tests"]
    for invalid in ["", "/absolute", "../outside", "docs/.svn/text", "https://svn.example/other"]:
        response = client.put(
            "/api/v1/smart-cases/knowledge-source",
            headers=admin_headers,
            json=_payload(include_paths=[invalid]),
        )
        assert response.status_code == 422

    legacy_payload = _payload(repository_urls=[])
    legacy_payload["repository_url"] = "http://svn.intranet.example/svn/legacy"
    legacy = client.put("/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=legacy_payload)
    assert legacy.status_code == 200
    assert legacy.json()["repository_urls"] == ["http://svn.intranet.example/svn/legacy"]

    saved = client.put(
        "/api/v1/smart-cases/knowledge-source",
        headers=admin_headers,
        json=_payload(repository_urls=[
            "http://svn.intranet.example/svn/knowledge",
            "https://svn.intranet.example/svn/archive",
        ]),
    )
    assert saved.status_code == 200
    assert saved.json()["repository_urls"] == [
        "http://svn.intranet.example/svn/knowledge",
        "https://svn.intranet.example/svn/archive",
    ]
    assert saved.json()["has_password"] is True
    assert "password" not in saved.text.replace("has_password", "")
    fetched = client.get("/api/v1/smart-cases/knowledge-source", headers=admin_headers)
    assert "svn-secret-value" not in fetched.text

    _add_model(
        client, admin_headers, "embedding", "http://embedding.intranet.example/v1", "bge-m3"
    )

    first = client.post("/api/v1/smart-cases/knowledge-source/sync", headers=admin_headers)
    second = client.post("/api/v1/smart-cases/knowledge-source/sync", headers=admin_headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert second.json()["reused"] is True

    db = SessionLocal()
    try:
        source = db.scalar(select(SvnKnowledgeSource))
        task = db.scalar(select(DurableTask).where(DurableTask.task_type == "svn_sync"))
        audits = list(db.scalars(select(AuditLog)).all())
        assert source is not None and source.encrypted_password != "svn-secret-value"
        assert task is not None and task.payload == {"source_id": source.id, "reason": "manual"}
        serialized = json.dumps([item.detail for item in audits], ensure_ascii=False)
        assert "svn-secret-value" not in serialized
    finally:
        db.close()


def test_connection_test_reuses_saved_password_without_exposing_it(client, admin_headers, monkeypatch) -> None:
    assert client.put(
        "/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload()
    ).status_code == 200
    received = {}

    def fake_test(repository_urls, username, password, include_paths):
        received.update(repository_urls=repository_urls, password=password, paths=include_paths)
        return {"ok": True, "svn_version": "1.10.0", "checked_paths": include_paths}

    monkeypatch.setattr("app.api.routes.smart_cases.test_svn_connection", fake_test)
    tested = client.post(
        "/api/v1/smart-cases/knowledge-source/connection-test",
        headers=admin_headers,
        json=_payload(password=None),
    )
    assert tested.status_code == 200
    assert tested.json()["checked_paths"] == ["docs/测试文档", "docs/需求文档"]
    assert received["repository_urls"] == ["http://svn.intranet.example/svn/knowledge"]
    assert received["password"] == "svn-secret-value"
    assert "svn-secret-value" not in tested.text


def test_connection_test_checks_repository_and_paths_without_listing() -> None:
    class RecordingClient:
        calls = []

        def version(self):
            return "1.10.0"

        def run(self, arguments, username, password):
            self.calls.append(arguments)
            return '<?xml version="1.0"?><info/>'

    runner = RecordingClient()
    result = check_svn_connection(
        ["http://svn.example/repository"],
        "readonly",
        "secret",
        ["公司制度"],
        runner,
    )
    assert result["checked_paths"] == ["公司制度"]
    assert runner.calls == [
        ["info", "--xml", "http://svn.example/repository"],
        [
            "info",
            "--xml",
            "http://svn.example/repository/%E5%85%AC%E5%8F%B8%E5%88%B6%E5%BA%A6",
        ],
    ]


def test_manifest_is_incremental_excludes_svn_and_keeps_source_revisions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "knowledge_root", tmp_path)
    working_copy = tmp_path / "wc"
    (working_copy / ".svn").mkdir(parents=True)
    (working_copy / ".svn" / "entries").write_text("private", encoding="utf-8")
    (working_copy / "cases").mkdir()
    document = working_copy / "cases" / "登录需求.md"
    document.write_text("first", encoding="utf-8")
    first, first_changes = _build_manifest(
        "http://svn.example/repo", {"docs/tests": "41"}, {"docs/tests": working_copy}, {}
    )
    second, second_changes = _build_manifest(
        "http://svn.example/repo", {"docs/tests": "42"}, {"docs/tests": working_copy}, first
    )
    assert list(second["files"]) == ["docs/tests/cases/登录需求.md"]
    assert first_changes["added"] == 1
    assert second_changes == {"added": 0, "changed": 0, "deleted": 0, "unchanged": 1}
    assert second["revisions"] == {"docs/tests": "42"}

    class FakeEmbedding:
        base_url = "http://embedding.example/v1"
        model = "bge-m3"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    dimensions = _publish_vector_index(first, {}, {"docs/tests": working_copy}, FakeEmbedding())
    results = search_vector_index("first", [1.0, 0.0], 5)
    assert dimensions == 2
    assert results[0]["source_path"] == "docs/tests/cases/登录需求.md"
    assert results[0]["revision"] == "41"
    requirements = list_indexed_requirements("登录")
    assert requirements[0]["requirement_name"] == "登录"


def test_generation_is_queued_from_an_indexed_requirement(client, admin_headers, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "knowledge_root", tmp_path)
    embedding_model, chat_model = _configure_models(client, admin_headers)
    assert client.put("/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload(include_paths=["docs/测试文档"])).status_code == 200
    working_copy = tmp_path / "wc"
    working_copy.mkdir()
    document = working_copy / "REQ-1024_登录需求.md"
    document.write_text("用户输入正确账号密码后进入首页", encoding="utf-8")
    manifest, _ = _build_manifest("http://svn.intranet.example/svn/knowledge", {"docs/测试文档": "51"}, {"docs/测试文档": working_copy}, {})

    class FakeEmbedding:
        base_url = "http://embedding.intranet.example/v1"
        model = "bge-m3"
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    _publish_vector_index(manifest, {}, {"docs/测试文档": working_copy}, FakeEmbedding())
    db = SessionLocal()
    try:
        source = db.scalar(select(SvnKnowledgeSource))
        source.last_revisions = {"docs/测试文档": "51"}
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/v1/smart-cases/requirements?query=REQ-1024", headers=admin_headers)
    assert listed.status_code == 200 and listed.json()[0]["requirement_no"] == "REQ-1024"
    created = client.post("/api/v1/smart-cases/generations", headers=admin_headers, json={"requirement_path": listed.json()[0]["source_path"]})
    assert created.status_code == 202 and created.json()["status"] == "queued"
    db = SessionLocal()
    try:
        task = db.scalar(select(DurableTask).where(DurableTask.task_type == "smart_case_generate"))
        generation = db.scalar(select(SmartCaseGeneration))
        assert task.payload == {"generation_id": generation.id}
        assert generation.ai_model_id == chat_model["id"]
        assert "用户输入" not in json.dumps(task.payload, ensure_ascii=False)
    finally:
        db.close()


def test_generated_case_json_requires_matching_steps_and_results() -> None:
    rows = parse_cases('{"cases":[{"title":"登录成功","preconditions":[],"steps":["登录"],"expected_results":["进入首页"]}]}')
    assert rows[0]["priority"] == "中"


def test_generation_preview_returns_saved_cases_only_for_completed_tasks(client, admin_headers) -> None:
    from app.models import User

    cases = parse_cases('{"cases":[{"title":"登录成功","preconditions":["账号已启用"],"steps":["登录"],"expected_results":["进入首页"]}]}')
    with SessionLocal() as db:
        item = SmartCaseGeneration(
            requirement_path="需求/登录.md", requirement_revision="51", requirement_no="1024",
            requirement_name="登录", llm_model="qwen3", status="succeeded", case_count=1,
            result_cases=cases, referenced_sources=[{"source_path": "需求/登录.md", "revision": "51"}],
            created_by=db.scalar(select(User.id).where(User.username == "admin")),
        )
        db.add(item)
        db.commit()
        generation_id = item.id
    url = f"/api/v1/smart-cases/generations/{generation_id}"
    assert client.get(url).status_code == 401
    response = client.get(url, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["result_cases"] == cases
    assert response.json()["referenced_sources"] == [{"source_path": "需求/登录.md", "revision": "51"}]
    assert "result_cases" not in client.get("/api/v1/smart-cases/generations", headers=admin_headers).json()[0]
    created = client.post("/api/v1/users", headers=admin_headers, json={
        "username": "case_tester", "password": "tester-password", "role": "tester", "display_name": "测试员",
    })
    assert created.status_code == 201
    token = client.post("/api/v1/auth/login", json={"username": "case_tester", "password": "tester-password"}).json()["access_token"]
    tester_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/smart-cases/generations", headers=tester_headers).json() == []
    assert client.get(url, headers=tester_headers).status_code == 404
    assert client.get(url + "/download", headers=tester_headers).status_code == 404
    with SessionLocal() as db:
        db.get(SmartCaseGeneration, generation_id).created_by = created.json()["id"]
        db.commit()
    assert client.get(url, headers=admin_headers).status_code == 404
    assert client.get(url + "/download", headers=admin_headers).status_code == 404
    assert len(client.get("/api/v1/smart-cases/generations", headers=tester_headers).json()) == 1
    assert client.get(url, headers=tester_headers).json()["result_cases"] == cases
    for status in ("queued", "running", "failed"):
        with SessionLocal() as db:
            db.get(SmartCaseGeneration, generation_id).status = status
            db.commit()
        assert client.get(url, headers=tester_headers).json()["result_cases"] == []
    assert client.get("/api/v1/smart-cases/generations/999999", headers=admin_headers).status_code == 404


def test_generated_excel_is_a_traceable_draft(tmp_path: Path) -> None:
    path = tmp_path / "cases.xlsx"
    generation = SimpleNamespace(
        requirement_no="REQ-1024", requirement_name="登录", requirement_path="需求/REQ-1024_登录.md",
        requirement_revision="51", llm_model="qwen3", referenced_sources=[{"source_path": "需求/REQ-1024_登录.md", "revision": "51"}],
    )
    build_workbook(path, generation, [{"title": "=登录", "preconditions": [], "steps": ["输入账号"], "expected_results": ["进入首页"], "case_type": "功能", "priority": "高"}])
    workbook = load_workbook(path, read_only=True)
    try:
        assert workbook["测试用例"]["D2"].value == "'=登录"
        assert workbook["测试用例"]["J2"].value == "草稿待复核"
        assert workbook["生成说明"]["B4"].value == "需求/REQ-1024_登录.md"
    finally:
        workbook.close()


def test_svn_password_only_enters_the_non_echoing_pty(tmp_path: Path) -> None:
    executable = tmp_path / "fake-svn"
    secret = "unique-pty-secret-92841"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import os, sys\n"
        "secret = 'unique-pty-secret-92841'\n"
        "if secret in ' '.join(sys.argv) or secret in '\\n'.join(os.environ.values()): sys.exit(9)\n"
        "print('<?xml version=\"1.0\"?><info>', end='', flush=True)\n"
        "terminal = os.open('/dev/tty', os.O_RDWR)\n"
        "os.write(terminal, b\"Password for 'readonly': \")\n"
        "entered = b''\n"
        "while not entered.endswith(b'\\n'): entered += os.read(terminal, 1)\n"
        "if entered.strip().decode() != secret: sys.exit(8)\n"
        "print('</info>')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    output = SvnClient(str(executable), timeout_seconds=5).run(["info", "--xml", "http://svn/repo"], "readonly", secret)
    assert _xml_root(output).tag == "info"
    assert secret not in output


def test_scheduled_sync_is_idempotent_per_thirty_minute_window(client, admin_headers) -> None:
    _add_model(
        client, admin_headers, "embedding", "http://embedding.intranet.example/v1", "bge-m3"
    )
    assert client.put(
        "/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload()
    ).status_code == 200
    now = beijing_now().replace(minute=1, second=0, microsecond=0)
    db = SessionLocal()
    try:
        first = enqueue_due_svn_syncs(db, now)
        assert first is not None
        first.status = "succeeded"
        db.commit()
        same_window = enqueue_due_svn_syncs(db, now + timedelta(minutes=10))
        assert same_window is not None and same_window.id == first.id
        next_window = enqueue_due_svn_syncs(db, now + timedelta(minutes=31))
        assert next_window is not None and next_window.id != first.id
        assert next_window.status == "queued"
    finally:
        db.close()


def test_scheduled_sync_waits_for_an_embedding_model(client, admin_headers) -> None:
    assert client.put(
        "/api/v1/smart-cases/knowledge-source", headers=admin_headers, json=_payload()
    ).status_code == 200
    db = SessionLocal()
    try:
        assert enqueue_due_svn_syncs(db) is None
        assert db.scalar(select(DurableTask).where(DurableTask.task_type == "svn_sync")) is None
    finally:
        db.close()


def test_svn_failure_preserves_diagnostic_without_password(tmp_path: Path) -> None:
    import pytest
    from app.services.svn_knowledge import SvnKnowledgeError

    executable = tmp_path / 'failed-svn'
    secret = 'private-svn-password'
    executable.write_text(
        '#!' + sys.executable + '\n'
        'import sys\n'
        'sys.stderr.write("svn: E000022: Cannot convert filename; private-svn-password\\n")\n'
        'sys.exit(1)\n', encoding='utf-8',
    )
    executable.chmod(0o700)
    with pytest.raises(SvnKnowledgeError) as error:
        SvnClient(str(executable), timeout_seconds=5).run(['info', 'http://svn/repo'], 'user', secret)
    assert 'E000022: Cannot convert filename' in str(error.value)
    assert secret not in str(error.value)
    assert '[REDACTED]' in str(error.value)


def test_svn_locale_supports_legacy_linux_and_overrides_ascii(monkeypatch) -> None:
    import pytest
    from app.services.svn_knowledge import _svn_environment, SvnKnowledgeError

    monkeypatch.setenv('LC_ALL', 'C')
    monkeypatch.setenv('LC_CTYPE', 'C')
    monkeypatch.setenv('LANGUAGE', 'zh_CN')
    monkeypatch.setattr('app.services.svn_knowledge.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=b'C\nPOSIX\nen_US.utf8\n'))
    environment = _svn_environment()
    assert 'LC_ALL' not in environment
    assert environment['LC_CTYPE'] == 'en_US.utf8'
    assert environment['LC_MESSAGES'] == environment['LANGUAGE'] == 'C'
    monkeypatch.setattr('app.services.svn_knowledge.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=b'C\nPOSIX\n'))
    with pytest.raises(SvnKnowledgeError, match='系统缺少 UTF-8 locale'):
        _svn_environment()


def test_svn_checkout_preserves_chinese_filenames(tmp_path: Path, monkeypatch) -> None:
    import shutil
    import subprocess
    import pytest
    from app.services.svn_knowledge import _svn_environment

    if not shutil.which('svn') or not shutil.which('svnadmin'):
        pytest.skip('requires svn and svnadmin')
    repository = tmp_path / 'repository'
    source = tmp_path / 'source'
    source.mkdir()
    filename = '公司报销制度（2020年）.pdf'
    (source / filename).write_bytes(b'test content')
    subprocess.run(['svnadmin', 'create', str(repository)], check=True, capture_output=True)
    subprocess.run(['svn', 'import', str(source), repository.as_uri(), '-m', 'test'], env=_svn_environment(), check=True, capture_output=True)
    monkeypatch.setenv('LC_ALL', 'C')
    destination = tmp_path / 'checkout'
    SvnClient(timeout_seconds=10).run(['checkout', repository.as_uri(), str(destination)], 'user', '')
    assert (destination / filename).read_bytes() == b'test content'


def test_svn_locale_command_handles_non_utf8_output(tmp_path: Path, monkeypatch) -> None:
    from app.services.svn_knowledge import _svn_environment

    executable = tmp_path / 'locale'
    executable.write_text(
        '#!' + sys.executable + '\n'
        'import sys\n'
        "sys.stdout.buffer.write(b'C\\ninvalid-\\xe5\\xff\\nzh_CN.gbk\\nen_US.utf8\\n')\n"
        "sys.stderr.buffer.write(b'warning: \\xe5\\xff')\n",
        encoding='utf-8',
    )
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path))
    environment = _svn_environment()
    assert environment['LC_CTYPE'] == 'en_US.utf8'
    assert environment['LC_MESSAGES'] == 'C'


def test_cancel_sync_handles_queued_and_running_tasks(client, admin_headers) -> None:
    from app.services.svn_knowledge import execute_svn_sync

    client.put('/api/v1/smart-cases/knowledge-source', headers=admin_headers, json=_payload())
    _add_model(client, admin_headers, 'embedding', 'http://embedding.intranet.example/v1', 'bge-m3')
    first = client.post('/api/v1/smart-cases/knowledge-source/sync', headers=admin_headers).json()
    assert client.post('/api/v1/smart-cases/knowledge-source/sync/cancel', headers=admin_headers).json()['status'] == 'cancelled'
    with SessionLocal() as db:
        assert db.get(DurableTask, first['task_id']).status == 'cancelled'
    second = client.post('/api/v1/smart-cases/knowledge-source/sync', headers=admin_headers).json()
    with SessionLocal() as db:
        task = db.get(DurableTask, second['task_id'])
        task.status = 'running'
        source_id = task.payload['source_id']
        db.commit()
    assert client.post('/api/v1/smart-cases/knowledge-source/sync/cancel', headers=admin_headers).json()['status'] == 'cancelling'
    assert client.get('/api/v1/smart-cases/knowledge-source/sync-status', headers=admin_headers).json()['status'] == 'cancelling'
    execute_svn_sync(source_id, task_id=second['task_id'])
    with SessionLocal() as db:
        assert db.get(DurableTask, second['task_id']).status == 'cancelled'
        assert db.get(SvnKnowledgeSource, source_id).sync_status == 'cancelled'


def test_cancel_svn_process_stops_child(tmp_path: Path) -> None:
    import os
    import pytest
    from app.services.svn_knowledge import SvnSyncCancelled

    executable = tmp_path / 'slow-svn'
    pid_file = tmp_path / 'pid'
    executable.write_text('#!' + sys.executable + '\nimport os, time\n'
                          + 'open(' + repr(str(pid_file)) + ', "w").write(str(os.getpid()))\ntime.sleep(30)\n')
    executable.chmod(0o700)
    runner = SvnClient(str(executable), timeout_seconds=5)
    def cancel():
        if pid_file.exists():
            raise SvnSyncCancelled('同步已取消')
    runner.check_cancelled = cancel
    with pytest.raises(SvnSyncCancelled):
        runner.run(['checkout', 'http://svn/repo'], 'user', '')
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_cancel_index_keeps_published_snapshot(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from app.services.svn_knowledge import SvnSyncCancelled

    monkeypatch.setattr(settings, 'knowledge_root', tmp_path)
    working = tmp_path / 'wc'
    working.mkdir()
    document = working / 'test.md'
    document.write_text('original')
    class Embedding:
        base_url = 'https://example.com/v1'
        model = 'test'
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]
    manifest, _ = _build_manifest('http://svn/repo', {'docs': '1'}, {'docs': working}, {})
    _publish_vector_index(manifest, {}, {'docs': working}, Embedding())
    destination = tmp_path / 'published' / 'svn-index.sqlite3'
    original = destination.read_bytes()
    def cancel():
        raise SvnSyncCancelled('同步已取消')
    with pytest.raises(SvnSyncCancelled):
        _publish_vector_index(manifest, manifest, {'docs': working}, Embedding(), cancel)
    assert destination.read_bytes() == original
    assert not list(destination.parent.glob('*.tmp'))
