from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT/'backend/app').rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))

jobs=(ROOT/'backend/app/api/jobs.py').read_text(encoding='utf-8')
providers=(ROOT/'backend/app/services/providers.py').read_text(encoding='utf-8')
quota=(ROOT/'backend/app/services/quota.py').read_text(encoding='utf-8')
tm=(ROOT/'backend/app/services/translation_memory.py').read_text(encoding='utf-8')
history=(ROOT/'backend/app/api/history.py').read_text(encoding='utf-8')
bootstrap=(ROOT/'backend/app/bootstrap.py').read_text(encoding='utf-8')
script=(ROOT/'scripts/rebuild.sh').read_text(encoding='utf-8')
update_script=(ROOT/'scripts/update.sh').read_text(encoding='utf-8')
ui=(ROOT/'user-frontend/src/App.jsx').read_text(encoding='utf-8')

for token in ['@router.post("/lookup"', 'document_doi', 'shared translation cache hit', 'reused_from_job_id']:
    assert token in jobs, token
for token in ['translation_memory.get', 'translation_memory.put', 'quota_manager.eligible', 'quota_manager.record_success', 'dispatch_weight', '54004', 'unsynchronized']:
    assert token in providers or token in quota, token
for token in ['class TranslationMemory', 'source_hash', 'hit_count']:
    assert token in tm, token
for token in ['/translation-memory', '/documents']:
    assert token in history, token
assert 'cfg["qps"] = 10' in bootstrap
assert '"qps": 5, "max_concurrency": 5' in bootstrap
for token in ['--remove-orphans','docker builder prune','--no-cache','--reset-data','--fresh']:
    assert token in script, token
for token in ['docker compose restart','backend/app','ZFT pre-upgrade database backup','docker compose build --progress=plain']:
    assert token in update_script, token
assert 'docker builder prune' not in update_script
assert 'docker compose build --no-cache' not in update_script
for token in ['默认翻译池','API Base URL','quota_total_chars']:
    assert token in ui or token in providers or token in quota, token

assert '"54004"' in providers and 'raise ValueError(message)' in providers
print('adaptive-history-smoke: ok')
