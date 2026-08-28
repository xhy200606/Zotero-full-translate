from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
services = compose.get('services') or {}
assert list(services) == ['zft'], services
assert services['zft']['ports'] == ['${ZFT_PORT:-3005}:8089']

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

api = (ROOT / 'frontend/src/api.js').read_text()
assert 'window.location.origin' in api
assert "['8000','8089']" in api

main = (ROOT / 'backend/app/main.py').read_text()
assert 'StaticFiles' in main
assert 'manager.start()' in main

print('single-container-smoke: ok')
