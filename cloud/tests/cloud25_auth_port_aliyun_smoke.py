from __future__ import annotations

import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix='zft-cloud25-smoke-'))
os.environ['DATABASE_URL'] = f"sqlite:///{root/'test.db'}"
os.environ['ZFT_DATA_DIR'] = str(root)
os.environ['ZFT_STORAGE_DIR'] = str(root/'files')
os.environ['ZFT_WORK_DIR'] = str(root/'work')
os.environ['ZFT_STATIC_DIR'] = str(root/'user')
os.environ['ZFT_ADMIN_STATIC_DIR'] = str(root/'admin')
os.environ['ZFT_CONFIG_SECRET'] = 'test-config-secret-please-change-123456'
os.environ['ZFT_BOOTSTRAP_ADMIN_PASSWORD'] = ''
os.environ['ZFT_ADMIN_PORT'] = '3006'
(root/'user').mkdir(parents=True, exist_ok=True)
(root/'admin').mkdir(parents=True, exist_ok=True)
(root/'user'/'index.html').write_text('USER', encoding='utf-8')
(root/'admin'/'index.html').write_text('ADMIN', encoding='utf-8')

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import AuthToken, Device, User
from app.services.providers import AliyunTranslator

with TestClient(app) as client:
    payload = {'username':'owner','password':'password-12345','device_code':'shared-browser-1','device_name':'Web portal','platform':'Linux x86_64','app_version':'web-2.5.2'}
    r = client.post('/api/v1/auth/register', json=payload)
    assert r.status_code == 200, r.text
    # Re-login on the same browser/device rotates the token instead of stacking sessions.
    r = client.post('/api/v1/auth/login', json=payload)
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == 'owner'))
        device = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_code == 'shared-browser-1'))
        active = int(db.scalar(select(func.count()).select_from(AuthToken).where(AuthToken.device_id == device.id, AuthToken.revoked_at.is_(None))) or 0)
        assert active == 1, active

    # The same physical browser may also hold the admin-console cookie on port
    # 3006. A different portal label must coexist without creating a second
    # Device row or revoking the user-portal token.
    admin_payload = dict(payload, device_name='Cloud Admin Console', app_version='admin-web-2.5.2')
    r = client.post('/api/v1/auth/login', json=admin_payload, headers={'host': 'testserver:3006'})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == 'owner'))
        device = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_code == 'shared-browser-1'))
        device_rows = int(db.scalar(select(func.count()).select_from(Device).where(Device.user_id == user.id)) or 0)
        active = int(db.scalar(select(func.count()).select_from(AuthToken).where(AuthToken.device_id == device.id, AuthToken.revoked_at.is_(None))) or 0)
        assert device_rows == 1, device_rows
        assert active == 2, active

    # Return to the user portal and verify the same effective client remains.
    r = client.post('/api/v1/auth/login', json=payload, headers={'host': 'testserver:3005'})
    assert r.status_code == 200, r.text
    devices = client.get('/api/v1/account/devices', headers={'host': 'testserver:3005'}).json()
    assert len(devices) == 1 and devices[0]['current'] is True, devices

    # Simulate the legacy per-port localStorage UUID, then send it as an alias
    # when the browser adopts the shared cross-port cookie.
    legacy_payload = dict(payload, device_code='legacy-admin-browser', device_name='Cloud Admin Console', app_version='admin-web-2.4.0')
    assert client.post('/api/v1/auth/login', json=legacy_payload, headers={'host': 'testserver:3006'}).status_code == 200
    migrate_payload = dict(payload, device_aliases=['legacy-admin-browser'], device_name='Cloud Admin Console', app_version='admin-web-2.5.2')
    assert client.post('/api/v1/auth/login', json=migrate_payload, headers={'host': 'testserver:3006'}).status_code == 200
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == 'owner'))
        legacy = db.scalar(select(Device).where(Device.user_id == user.id, Device.device_code == 'legacy-admin-browser'))
        assert legacy is not None and legacy.revoked is True
    assert client.post('/api/v1/auth/login', json=payload, headers={'host': 'testserver:3005'}).status_code == 200
    devices = client.get('/api/v1/account/devices', headers={'host': 'testserver:3005'}).json()
    assert len(devices) == 1, devices

    providers = client.get('/api/v1/account/providers', headers={'host': 'testserver:3005'})
    assert providers.status_code == 200, providers.text
    by_id = {p['id']: p for p in providers.json()}
    assert 'baidu_machine' not in by_id, by_id.keys()
    assert 'baidu_llm' in by_id, by_id.keys()
    assert by_id['baidu_llm']['config']['model_type'] == 'llm'
    assert 'aliyun_professional' in by_id, by_id.keys()
    pro = by_id['aliyun_professional']
    assert pro['config']['api_mode'] == 'rpc'
    assert pro['config']['action'] == 'Translate'
    assert pro['config']['scene'] == 'description'

    # Logout removes the stale web device from the "effective clients" list.
    assert client.post('/api/v1/auth/logout').status_code == 200
    # Log back in so we can query the account; stale sessions are still hidden.
    assert client.post('/api/v1/auth/login', json=payload).status_code == 200
    devices = client.get('/api/v1/account/devices').json()
    assert len(devices) == 1, devices

# Validate Aliyun professional RPC request shape without making a network call.
translator = AliyunTranslator(
    'en','zh-CN','user:u:aliyun_professional',50,10,
    'https://mt.cn-hangzhou.aliyuncs.com','akid','aksecret',
    scene='medical', max_chars=4900, api_mode='rpc', action='Translate', context='paper context'
)
class FakeResponse:
    status_code = 200
    text = ''
    headers = {}
    def raise_for_status(self): return None
    def json(self): return {'Code': 200, 'Message': 'success', 'Data': {'Translated': '你好'}}
class FakeClient:
    def __init__(self): self.last = None
    def post(self, url, **kwargs):
        self.last = (url, kwargs)
        return FakeResponse()
fake = FakeClient()
translator.client = fake
assert translator._rpc_one('Hello') == '你好'
url, kwargs = fake.last
form = kwargs['data']
assert url == 'https://mt.cn-hangzhou.aliyuncs.com/'
assert form['Action'] == 'Translate' and form['Version'] == '2018-10-12'
assert form['Scene'] == 'medical' and form['Context'] == 'paper context'
assert form['SourceLanguage'] == 'en' and form['TargetLanguage'] == 'zh'
assert form['Signature']

print('cloud25-auth-port-aliyun-smoke: ok')
