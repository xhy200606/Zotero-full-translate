from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

# Python syntax
for path in (ROOT / "backend/app").rglob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

# v1.1 cloud contracts
jobs = (ROOT / "backend/app/api/jobs.py").read_text(encoding="utf-8")
for needle in ["client_request_id", "/timeline", "/retry", "active_only"]:
    assert needle in jobs, needle
providers = (ROOT / "backend/app/api/providers.py").read_text(encoding="utf-8")
assert "test_provider_endpoint" in providers
assert "encrypt_json" in providers
rate = (ROOT / "backend/app/services/rate_limit.py").read_text(encoding="utf-8")
assert "GlobalRateGate" in rate and "metrics_scope" in rate and "register_script" not in rate

# Material 3 UI contract
css = (ROOT / "frontend/src/style.css").read_text(encoding="utf-8")
for needle in [
    "--md-sys-color-primary",
    "--md-sys-color-surface-container",
    "--md-sys-color-secondary-container",
    "md-nav-bar",
    "md-nav-rail",
    "md-nav-drawer",
    "task-list-detail",
    "@media (min-width:600px)",
    "@media (min-width:840px)",
    "@media (min-width:1200px)",
]:
    assert needle in css, needle

app = (ROOT / "frontend/src/App.jsx").read_text(encoding="utf-8")
for view in ["overview", "tasks", "services", "runtime", "settings"]:
    assert view in app, view

print("zft-cloud-v1.4.1-smoke: ok")
