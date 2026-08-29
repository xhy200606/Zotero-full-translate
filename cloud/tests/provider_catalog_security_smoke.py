from __future__ import annotations

import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-provider-security-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root/'test.db'}"
os.environ["ZFT_DATA_DIR"] = str(root)
os.environ["ZFT_STORAGE_DIR"] = str(root/'files')
os.environ["ZFT_WORK_DIR"] = str(root/'work')
os.environ["ZFT_STATIC_DIR"] = str(root/'user')
os.environ["ZFT_ADMIN_STATIC_DIR"] = str(root/'admin')
os.environ["ZFT_CONFIG_SECRET"] = "test-config-secret-please-change"
os.environ["ZFT_BOOTSTRAP_ADMIN_PASSWORD"] = ""
os.environ["ZFT_PUBLIC_HARDENING"] = "true"
os.environ["ZFT_EXPOSE_API_DOCS"] = "false"
os.environ["ZFT_ALLOW_PRIVATE_PROVIDER_ENDPOINTS"] = "false"
os.environ["ZFT_ALLOW_INSECURE_PROVIDER_HTTP"] = "false"
(root/'user').mkdir(parents=True, exist_ok=True)
(root/'admin').mkdir(parents=True, exist_ok=True)
(root/'user'/'index.html').write_text('USER', encoding='utf-8')
(root/'admin'/'index.html').write_text('ADMIN', encoding='utf-8')

from fastapi.testclient import TestClient
from app.main import app
from app.services.providers import BaiduTranslator

with TestClient(app) as client:
    r = client.get('/health')
    assert r.status_code == 200
    assert r.headers.get('x-frame-options') == 'DENY'
    assert r.headers.get('x-content-type-options') == 'nosniff'
    assert 'frame-ancestors' in r.headers.get('content-security-policy', '')
    assert client.get('/docs').status_code == 200 and client.get('/docs').text == 'USER'

    r = client.post('/api/v1/auth/register', json={'username':'owner','password':'password-12345','device_code':'web-a','device_name':'Browser'})
    assert r.status_code == 200, r.text
    catalog = client.get('/api/v1/account/providers/catalog')
    assert catalog.status_code == 200, catalog.text
    ids = {x['template_id'] for x in catalog.json()}
    for expected in ('baidu_general','baidu_machine','baidu_domain','baidu_llm','custom_openai_compatible'):
        assert expected in ids, (expected, ids)

    created = client.post('/api/v1/account/providers', json={'template_id':'baidu_domain'})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body['template_id'] == 'baidu_domain'
    assert body['config']['service_type'] == 'domain'
    assert body['credential_url'].startswith('https://')

    custom = client.post('/api/v1/account/providers', json={'template_id':'custom_openai_compatible','display_name':'Private SSRF test'})
    assert custom.status_code == 200, custom.text
    pid = custom.json()['id']
    blocked = client.put(f'/api/v1/account/providers/{pid}', json={'config':{'base_url':'http://127.0.0.1:8080/v1'}})
    assert blocked.status_code == 400, blocked.text
    blocked = client.put(f'/api/v1/account/providers/{pid}', json={'config':{'base_url':'https://127.0.0.1/v1'}})
    assert blocked.status_code == 400, blocked.text

    cross = client.post('/api/v1/auth/logout', headers={'Origin':'https://evil.example'})
    assert cross.status_code == 403, cross.text

sign = BaiduTranslator('en','zh-CN','p',10,10,'appid','https://fanyi-api.baidu.com/api/trans/vip/fieldtranslate',secret_key='secret',service_type='domain',domain='academic')
body, headers = sign._request_payload('hello')
assert body['domain'] == 'academic' and len(body['sign']) == 32 and not headers
machine = BaiduTranslator('en','zh-CN','p',10,10,'appid','https://fanyi-api.baidu.com/ait/api/aiTextTranslate',auth_mode='api_key',api_key='key',model_type='nmt',service_type='machine')
body, headers = machine._request_payload('hello')
assert body['model_type'] == 'nmt' and headers['Authorization'] == 'Bearer key'

for index in ('user-frontend/index.html','admin-frontend/index.html'):
    text=(Path(__file__).resolve().parents[1]/index).read_text()
    assert '/favicon.svg' in text

print('provider-catalog-security-smoke: ok')
