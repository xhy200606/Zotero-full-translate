from __future__ import annotations

import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-provider-pool-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root/'test.db'}"
os.environ["ZFT_DATA_DIR"] = str(root)
os.environ["ZFT_STORAGE_DIR"] = str(root/'files')
os.environ["ZFT_WORK_DIR"] = str(root/'work')
os.environ["ZFT_STATIC_DIR"] = str(root/'user')
os.environ["ZFT_ADMIN_STATIC_DIR"] = str(root/'admin')
os.environ["ZFT_CONFIG_SECRET"] = "provider-pool-test-secret-1234567890"
os.environ["ZFT_BOOTSTRAP_ADMIN_PASSWORD"] = ""
(root/'user').mkdir(parents=True, exist_ok=True)
(root/'admin').mkdir(parents=True, exist_ok=True)
(root/'user'/'index.html').write_text('USER', encoding='utf-8')
(root/'admin'/'index.html').write_text('ADMIN', encoding='utf-8')

from fastapi.testclient import TestClient
from app.main import app
from app.services.quota import quota_manager

with TestClient(app) as client:
    auth = client.post('/api/v1/auth/register', json={
        'username':'poolowner','password':'password-12345','device_code':'browser-pool','device_name':'Web portal'
    })
    assert auth.status_code == 200, auth.text

    ids=[]
    for name, qps, total in [('百度账号 A', 3, 1000000), ('百度账号 B', 7, 2000000)]:
        created = client.post('/api/v1/account/providers', json={'template_id':'baidu_llm','display_name':name})
        assert created.status_code == 200, created.text
        pid=created.json()['id']
        ids.append(pid)
        saved=client.put(f'/api/v1/account/providers/{pid}', json={
            'enabled':True,
            'config':{'qps':qps,'max_concurrency':qps,'quota_enabled':True,'quota_total_chars':total,'quota_low_percent':10},
            'secrets':{'app_id':f'app-{qps}','api_key':f'key-{qps}'}
        })
        assert saved.status_code == 200 and saved.json()['configured'] is True, saved.text

    # Simulate one account entering its configured low-quota threshold.
    quota_manager.record_success(f'user:{auth.json()["user"]["id"]}:{ids[0]}', 950000, {'quota_enabled':True,'quota_total_chars':1000000,'quota_low_percent':10})

    defaults=client.put('/api/v1/account/providers/settings/default', json={
        'default_provider_ids':ids,'default_provider_strategy':'balanced'
    })
    assert defaults.status_code == 200, defaults.text
    key=client.post('/api/v1/account/api-keys', json={
        'label':'Zotero','scopes':['translate','lookup','download']
    }).json()['api_key']
    headers={'Authorization':f'Bearer {key}','X-ZFT-Device-ID':'zotero-pool','X-ZFT-Client-Version':'0.4.2'}
    pool=client.get('/api/v1/account/provider-pool',headers=headers)
    assert pool.status_code == 200, pool.text
    data=pool.json()
    rows=[x for x in data['items'] if x['id'] in ids]
    assert len(rows)==2, data
    assert {x['display_name'] for x in rows} == {'百度账号 A','百度账号 B'}
    assert [x for x in data['default_provider_ids'] if x in ids] == ids
    assert data['default_provider_strategy'] == 'balanced'
    assert {int(x['qps']) for x in rows} == {3,7}
    assert {int(x['quota_total_chars']) for x in rows} == {1000000,2000000}
    by_name={x['display_name']:x for x in rows}
    assert int(by_name['百度账号 A']['quota_remaining_chars']) == 50000
    assert float(by_name['百度账号 A']['quota_remaining_percent']) == 5.0
    assert by_name['百度账号 A']['quota_status'] == 'low'
    assert int(by_name['百度账号 B']['quota_remaining_chars']) == 2000000
    assert float(by_name['百度账号 B']['quota_remaining_percent']) == 100.0
    assert all(float(x['quota_low_percent']) == 10.0 for x in rows)

print('client-provider-pool-smoke: ok')
