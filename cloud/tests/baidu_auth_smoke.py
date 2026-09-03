from pathlib import Path
root=Path(__file__).resolve().parents[1]
providers=(root/'backend/app/services/providers.py').read_text()
api=(root/'backend/app/api/providers.py').read_text()
ui=(root/'user-frontend/src/App.jsx').read_text()
assert 'api/trans/vip/translate' in providers
assert 'ait/api/aiTextTranslate' in providers
assert 'Authorization' in providers and 'Bearer' in providers
assert 'auth_mode' in providers and 'api_key' in providers
assert '不要把“API Key管理”生成的 API Key 填到密钥字段' in providers
assert '{"app_id", "secret_key", "api_key"}' in api
assert 'APPID' in ui and 'Secret Key' in ui
assert 'API Key' in ui and '翻译领域' in ui
assert '"baidu_machine": {' not in (root/'backend/app/services/user_providers.py').read_text()
assert '"baidu_llm": {' in (root/'backend/app/services/user_providers.py').read_text()
assert 'baidu_domain' in (root/'backend/app/services/user_providers.py').read_text()
assert 'fieldtranslate' in providers and 'service_type' in providers
print('baidu-auth-smoke: ok')
