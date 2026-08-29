from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ui=(ROOT/'user-frontend/src/App.jsx').read_text()
for forbidden in [
    'BabelDOC 负责', 'manual_budget', 'local_meter', '服务器是唯一限速源',
    '多引擎 QPS 相加后仍不能超过此值', '大型论文自动拆分',
    '内置持久任务队列等待任务', 'current_2025', 'legacy_2020',
]:
    assert forbidden not in ui, forbidden
assert '火山机器翻译' in ui
assert "['endpoint','API 地址','text']" in ui
assert "['resource_id','Resource ID','text']" in ui
assert 'ProviderLogo' in ui and 'company-baidu' in (ROOT/'user-frontend/src/style.css').read_text()
assert '获取 API' in ui
print('frontend-clean-smoke: ok')
style=(ROOT/'user-frontend/src/style.css').read_text()
assert '--md-sys-color-primary:#6750A4' in style
assert '--md-sys-color-surface:#FEF7FF' in style
assert '--md-sys-shape-corner-xl:28px' in style
assert 'grid-template-columns:repeat(6,minmax(0,1fr))' in style
assert '@media(min-width:600px)' in style and 'width:80px' in style
assert '@media(min-width:1200px)' in style and 'width:320px' in style
assert '#415f91' not in style.lower()
print('frontend-v1.4-visual-contract: ok')
