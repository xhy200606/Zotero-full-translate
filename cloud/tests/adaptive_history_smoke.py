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
ui=(ROOT/'frontend/src/App.jsx').read_text(encoding='utf-8')

for token in ['@router.post("/lookup"', 'source_sha256 = hashlib.sha256(data).hexdigest()', 'history cache hit', 'reused_from_job_id']:
    assert token in jobs, token
for token in ['translation_memory.get', 'translation_memory.put', 'quota_manager.eligible', 'quota_manager.record_success', 'dispatch_weight', '54004', 'unsynchronized']:
    assert token in providers or token in quota, token
for token in ['class TranslationMemory', 'source_hash', 'hit_count']:
    assert token in tm, token
for token in ['/translation-memory', '/documents']:
    assert token in history, token
assert 'cfg["qps"] = 10' in bootstrap
assert '"qps": 5, "max_concurrency": 5' in bootstrap
for token in ['--remove-orphans','docker builder prune','--no-cache','--reset-data','PRESERVED']:
    assert token in script, token
for token in ['额度感知调度','quota_total_chars','剩余']:
    assert token in ui or token in providers or token in quota, token

assert '"54004"' in providers and 'raise ValueError(message)' in providers
print('adaptive-history-smoke: ok')
