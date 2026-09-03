from __future__ import annotations

import os
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-baidu-cleanup-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root/'test.db'}"
os.environ["ZFT_DATA_DIR"] = str(root)
os.environ["ZFT_STORAGE_DIR"] = str(root/'files')
os.environ["ZFT_WORK_DIR"] = str(root/'work')
os.environ["ZFT_STATIC_DIR"] = str(root/'user')
os.environ["ZFT_ADMIN_STATIC_DIR"] = str(root/'admin')
os.environ["ZFT_CONFIG_SECRET"] = "baidu-cleanup-test-secret-1234567890"
os.environ["ZFT_BOOTSTRAP_ADMIN_PASSWORD"] = ""
(root/'user').mkdir(parents=True, exist_ok=True)
(root/'admin').mkdir(parents=True, exist_ok=True)
(root/'user'/'index.html').write_text('USER', encoding='utf-8')
(root/'admin'/'index.html').write_text('ADMIN', encoding='utf-8')

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import User, UserProviderProfile, UserTranslationSettings

with TestClient(app) as client:
    auth = client.post('/api/v1/auth/register', json={
        'username': 'cleanupowner',
        'password': 'password-12345',
        'device_code': 'cleanup-browser',
    })
    assert auth.status_code == 200, auth.text
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == 'cleanupowner'))
        db.add(UserProviderProfile(
            user_id=user.id,
            provider_id='baidu_machine',
            kind='baidu',
            display_name='legacy-baidu-profile',
            enabled=True,
            config={
                'template_id': 'baidu_machine',
                'service_type': 'machine',
                'auth_mode': 'api_key',
                'model_type': 'nmt',
                'endpoint': 'https://fanyi-api.baidu.com/ait/api/aiTextTranslate',
                'qps': 10,
                'max_concurrency': 10,
            },
        ))
        db.add(UserTranslationSettings(
            user_id=user.id,
            default_provider_ids=['baidu_machine'],
            default_provider_strategy='balanced',
        ))
        db.commit()

    catalog = client.get('/api/v1/account/providers/catalog').json()
    assert 'baidu_machine' not in {item['template_id'] for item in catalog}

    providers = client.get('/api/v1/account/providers').json()
    by_id = {item['id']: item for item in providers}
    assert 'baidu_machine' not in by_id
    assert by_id['baidu_llm']['config']['service_type'] == 'llm'
    assert by_id['baidu_llm']['config']['model_type'] == 'llm'

    settings = client.get('/api/v1/account/providers/settings/default').json()
    assert settings['default_provider_ids'] == ['baidu_llm']

print('baidu-catalog-cleanup-smoke: ok')
