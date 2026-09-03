from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

               
for path in (ROOT / "backend/app").rglob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

                                                               
jobs = (ROOT / "backend/app/api/jobs.py").read_text(encoding="utf-8")
for needle in ["document_doi", "shared_candidate_hidden", "force_retranslate", "/history", "/timeline", "/retry"]:
    assert needle in jobs, needle
auth = (ROOT / "backend/app/api/auth.py").read_text(encoding="utf-8")
for needle in ["/login", "/register", "too many login attempts", "/bootstrap"]:
    assert needle in auth, needle
admin = (ROOT / "backend/app/api/admin.py").read_text(encoding="utf-8")
for needle in ["/summary", "/users", "today_bytes", "AuthToken"]:
    assert needle in admin, needle

account = (ROOT / "backend/app/api/account.py").read_text(encoding="utf-8")
for needle in ["/api-keys", "issue_client_api_key", "require_web_session", "/rotate"]:
    assert needle in account, needle
bindings = (ROOT / "backend/app/services/document_bindings.py").read_text(encoding="utf-8")
for needle in ["resolve_bound_job", "bind_completed_job", "UserDocumentBinding", "TranslationVersion", "normalize_doi"]:
    assert needle in bindings, needle

                                    
for name in ["user-frontend", "admin-frontend"]:
    css = (ROOT / name / "src/style.css").read_text(encoding="utf-8")
    assert "--md-sys-color-primary" in css
    assert "--md-sys-color-surface" in css

admin_app = (ROOT / "admin-frontend/src/App.jsx").read_text(encoding="utf-8")
for view in ["overview", "users", "settings"]:
    assert view in admin_app, view
for forbidden in ["ProviderSheet", "ServicesPage", "/api/v1/providers"]:
    assert forbidden not in admin_app, forbidden
user_app = (ROOT / "user-frontend/src/App.jsx").read_text(encoding="utf-8")
for needle in ["今日调用次数", "今日调用字节", "今日缓存复用", "Zotero API Key", "有效客户端", "翻译 API", "默认翻译池"]:
    assert needle in user_app, needle

print("zft-cloud-v2.5.2-smoke: ok")
