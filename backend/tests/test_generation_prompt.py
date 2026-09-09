from app.services.llm import generate_cases, DEFAULT_SYSTEM_PROMPT


def test_prompt_configuration_validates_persists_and_renders(client, admin_headers):
    path = '/api/v1/smart-cases/generation-prompt'
    assert client.get(path).status_code == 401
    defaults = client.get(path, headers=admin_headers).json()
    assert defaults['system_prompt'] == DEFAULT_SYSTEM_PROMPT
    for template in ['no context', '{{references}} {{unknown}}']:
        assert client.put(path, headers=admin_headers, json={'system_prompt': 'test', 'user_prompt': template}).status_code == 422
    custom = {'system_prompt': '自定义系统测试规则', 'user_prompt': '需求 {{requirement_name}}\n{{references}}\n输出 {"cases":[]}'}
    assert client.put(path, headers=admin_headers, json=custom).status_code == 200
    saved = client.get(path, headers=admin_headers).json()
    assert saved['user_prompt'] == custom['user_prompt']
    class Model:
        def complete(self, messages):
            assert messages[0]['content'] == custom['system_prompt']
            assert '需求 登录需求' in messages[1]['content']
            assert '来源：req.md（r1）\n正文 {{requirement_name}}' in messages[1]['content']
            assert '输出 {"cases":[]}' in messages[1]['content']
            return '{"cases":[{"title":"登录","steps":["提交"],"expected_results":["成功"]}]}'
    assert generate_cases(Model(), {'requirement_name': '登录需求'}, [{'source_path': 'req.md', 'revision': '1', 'content': '正文 {{requirement_name}}'}], saved['system_prompt'], saved['user_prompt'])[0]['title'] == '登录'
    assert client.put(path, headers=admin_headers, json={'system_prompt': defaults['default_system_prompt'], 'user_prompt': defaults['default_user_prompt']}).status_code == 200
