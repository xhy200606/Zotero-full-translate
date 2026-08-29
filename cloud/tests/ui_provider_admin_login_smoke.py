from pathlib import Path
root=Path(__file__).resolve().parents[2]
user=(root/'cloud/user-frontend/src/App.jsx').read_text()
ucss=(root/'cloud/user-frontend/src/style.css').read_text()
admin=(root/'cloud/admin-frontend/src/App.jsx').read_text()
acss=(root/'cloud/admin-frontend/src/style.css').read_text()
assert 'provider-info-card' in user and '默认翻译池' in user
assert 'AddProviderDialog' in user and 'ProviderSettingsSheet' in user
assert 'provider-card-grid' in ucss and 'provider-catalog-grid' in ucss
assert 'admin-login-stage' in admin and 'login-stat-grid' in admin
assert "api('/health')" in admin
assert 'admin-login-stage' in acss and 'login-stat' in acss
print('ui-provider-admin-login-smoke: ok')
