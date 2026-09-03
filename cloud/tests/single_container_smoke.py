from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
services = compose.get('services') or {}
assert list(services) == ['zft'], services
assert services['zft']['ports'] == ['${ZFT_BIND:-0.0.0.0}:${ZFT_PORT:-3005}:8089', '${ZFT_ADMIN_BIND:-0.0.0.0}:${ZFT_ADMIN_PORT:-3006}:8089']
assert services['zft']['security_opt'] == ['no-new-privileges:true']
assert services['zft']['cap_drop'] == ['ALL']
assert services['zft']['environment']['ZFT_ADMIN_STATIC_DIR'] == '/app/static/admin'

req = (ROOT / 'backend/requirements.txt').read_text()
for forbidden in ('celery==', 'redis==', 'minio==', 'psycopg', 'cryptography==45.0.6'):
    assert forbidden not in req, forbidden

for path in (ROOT / 'backend/app').rglob('*.py'):
    if path.name == '__init__.py':
        continue
    text = path.read_text()
    assert 'from celery' not in text, path
    assert 'import redis' not in text, path
    assert 'from minio' not in text, path

user_api = (ROOT / 'user-frontend/src/api.js').read_text()
admin_api = (ROOT / 'admin-frontend/src/api.js').read_text()
assert 'window.location.origin' in user_api
assert 'window.location.origin' in admin_api
assert "credentials:'include'" in user_api
assert "credentials:'include'" in admin_api
assert 'zft_auth_token' not in user_api
assert 'zft_admin_token' not in admin_api

main = (ROOT / 'backend/app/main.py').read_text()
assert 'FileResponse' in main
assert 'zft_admin_static_dir' in main
assert 'manager.start()' in main
assert 'allow_credentials=True' in main
assert 'alembic==1.14.1' in req

print('single-container-smoke: ok')
