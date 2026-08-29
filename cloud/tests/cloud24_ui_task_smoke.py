from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

root=Path(tempfile.mkdtemp(prefix='zft-cloud24-smoke-'))
os.environ['DATABASE_URL']=f"sqlite:///{root/'test.db'}"
os.environ['ZFT_DATA_DIR']=str(root)
os.environ['ZFT_STORAGE_DIR']=str(root/'files')
os.environ['ZFT_WORK_DIR']=str(root/'work')
os.environ['ZFT_STATIC_DIR']=str(root/'user')
os.environ['ZFT_ADMIN_STATIC_DIR']=str(root/'admin')
os.environ['ZFT_API_KEY']='service-key-for-test'
os.environ['ZFT_CONFIG_SECRET']='test-config-secret-please-change'
os.environ['ZFT_BOOTSTRAP_ADMIN_PASSWORD']=''

(root/'user').mkdir(parents=True,exist_ok=True)
(root/'admin').mkdir(parents=True,exist_ok=True)
(root/'user'/'index.html').write_text('USER',encoding='utf-8')
(root/'admin'/'index.html').write_text('ADMIN',encoding='utf-8')

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Job,User
from app.services.document_bindings import bind_completed_job
from app.storage import path_for,put_bytes

with TestClient(app) as client:
    r=client.post('/api/v1/auth/register',json={'username':'owner','password':'password-12345','device_code':'web-a','device_name':'Browser A'})
    assert r.status_code==200,r.text
    r=client.post('/api/v1/auth/login',json={'username':'owner','password':'password-12345','device_code':'web-b','device_name':'Browser B'})
    assert r.status_code==200,r.text
    devices=client.get('/api/v1/account/devices').json()
    old=next(x for x in devices if x['device_code']=='web-a')
    assert old['current'] is False
    assert client.delete(f"/api/v1/account/devices/{old['id']}").status_code==200
    devices=client.get('/api/v1/account/devices').json()
    assert all(x['device_code']!='web-a' for x in devices)

    provider=client.get('/api/v1/account/providers').json()[0]
    quota=client.get(f"/api/v1/account/providers/{provider['id']}/quota")
    assert quota.status_code==200 and 'used_chars' in quota.json(),quota.text
    reset=client.post(f"/api/v1/account/providers/{provider['id']}/quota/reset")
    assert reset.status_code==200 and reset.json()['used_chars']==0,reset.text


    created=client.post('/api/v1/account/api-keys',json={'label':'temporary','scopes':['translate','lookup','download','account:read']})
    assert created.status_code==200,created.text
    temp_id=created.json()['id']
    assert client.delete(f'/api/v1/account/api-keys/{temp_id}').status_code==200
    assert all(x['id']!=temp_id for x in client.get('/api/v1/account/api-keys').json())

    import app.api.providers as providers_api
    provider=client.get('/api/v1/account/providers').json()[0]
    configured=client.put(f"/api/v1/account/providers/{provider['id']}",json={
        'enabled':True,
        'config':{'base_url':'https://example.invalid/v1','model':'test-model'},
        'secrets':{'api_key':'test-provider-secret'},
    })
    assert configured.status_code==200,configured.text
    original_test_provider=providers_api.test_provider
    providers_api.test_provider=lambda provider_id,user_id=None:'你好'
    try:
        tested=client.post(f"/api/v1/account/providers/{provider['id']}/test")
        assert tested.status_code==200,tested.text
        assert tested.json()['ok'] is True and tested.json()['sample']=='你好',tested.text
    finally:
        providers_api.test_provider=original_test_provider

    created=client.post('/api/v1/account/api-keys',json={'label':'Zotero','scopes':['translate','lookup','download','account:read']})
    assert created.status_code==200,created.text
    key=created.json()['api_key']
    headers={'Authorization':f'Bearer {key}','X-ZFT-Device-ID':'zotero-a'}
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username=='owner'))
        job_id=uuid.uuid4().hex
        mono_key=f'results/{job_id}/mono.pdf'
        put_bytes(mono_key,b'%PDF-1.4\ntranslated\n%%EOF\n','application/pdf')
        job=Job(id=job_id,filename='history.pdf',status='COMPLETED',stage='completed',progress=100,stage_progress=100,lang_in='en',lang_out='zh-CN',pages=None,output_mode='mono',provider='openai_compatible',provider_ids=['openai_compatible'],provider_strategy='single',qps=1,pool_workers=1,user_id=user.id,source_key=f'doi/10.5555/delete.test',document_doi='10.5555/delete.test',mono_key=mono_key,result_bytes=28)
        db.add(job)
        db.flush()
        bind_completed_job(db,job,commit=False)
        db.commit()
    assert path_for(mono_key).is_file()
    deleted=client.delete(f'/api/v1/jobs/{job_id}/history',headers=headers)
    assert deleted.status_code==200,deleted.text
    with SessionLocal() as db:
        assert db.get(Job,job_id) is None
    assert not path_for(mono_key).exists()

app_src=(Path(__file__).parents[1]/'user-frontend'/'src'/'App.jsx').read_text(encoding='utf-8')
assert "document.execCommand('copy')" in app_src
assert "if(view!=='clients'){setCreatedApiKey('')" in app_src
assert "quota/reset" in app_src
assert "正在保存并测试" in app_src
assert "/history" in app_src and '翻译任务管理' in app_src
assert '今日翻译字符' in app_src and '实时 QPS' in app_src
print('cloud24-ui-task-smoke: ok')
