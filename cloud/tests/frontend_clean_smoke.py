from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ui=(ROOT/'frontend/src/App.jsx').read_text()
for forbidden in [
    'BabelDOC 负责', 'manual_budget', 'local_meter', '服务器是唯一限速源',
    '多引擎 QPS 相加后仍不能超过此值', '大型论文自动拆分',
    '内置持久任务队列等待任务', 'current_2025', 'legacy_2020',
]:
    assert forbidden not in ui, forbidden
assert 'https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate' in ui
assert 'volc.speech.mt' in ui
print('frontend-clean-smoke: ok')
