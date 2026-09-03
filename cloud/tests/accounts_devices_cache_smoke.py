"""Cloud 2.3 multi-user registration, per-user providers and DOI cache smoke test."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-account-v23-smoke-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root / 'test.db'}"
os.environ["ZFT_DATA_DIR"] = str(root)
os.environ["ZFT_STORAGE_DIR"] = str(root / "files")
os.environ["ZFT_WORK_DIR"] = str(root / "work")
os.environ["ZFT_STATIC_DIR"] = str(root / "user")
os.environ["ZFT_ADMIN_STATIC_DIR"] = str(root / "admin")
os.environ["ZFT_API_KEY"] = "service-key-for-test"
os.environ["ZFT_BOOTSTRAP_ADMIN_PASSWORD"] = ""
os.environ["ZFT_CONFIG_SECRET"] = "test-config-secret-please-change"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.db import SessionLocal
from app.models import Job, User, UserProviderProfile
from app.storage import put_bytes

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
TRANSLATED = b"%PDF-1.4\n% translated\n%%EOF\n"
DOI = "10.5555/zft.multiuser.2026"


def api_auth(key, device="zotero-device"):
    return {"Authorization": f"Bearer {key}", "X-ZFT-Device-ID": device, "X-ZFT-Client-Version":"0.4.1"}


def login(client, username, password="password-12345"):
    r = client.post("/api/v1/auth/login", json={"username":username,"password":password,"device_code":f"web-{username}","device_name":"Web portal"})
    assert r.status_code == 200, r.text
    assert "token" not in r.json()
    return r


(root / "user").mkdir(parents=True, exist_ok=True)
(root / "admin").mkdir(parents=True, exist_ok=True)
(root / "user" / "index.html").write_text("USER-PORTAL", encoding="utf-8")
(root / "admin" / "index.html").write_text("ADMIN-PORTAL", encoding="utf-8")

with TestClient(app) as client:
    caps = client.get("/api/v1/auth/capabilities")
    assert caps.json()["setup_required"] is True and caps.json()["registration_enabled"] is True

                                                                                  
                                     
    r = client.post("/api/v1/auth/register", json={"username":"owner","password":"password-12345","device_code":"owner-web","device_name":"Owner"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin", r.text
    assert "token" not in r.json()
    r = client.post("/api/v1/auth/register", json={"username":"visitor","password":"password-12345","device_code":"visitor-web","device_name":"Visitor"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "user", r.text

                                                                     
    service = {"Authorization":"Bearer service-key-for-test"}
    for username in ("alice", "bob"):
        r = client.post("/api/v1/admin/users", headers=service, json={"username":username,"password":"password-12345","role":"user"})
        assert r.status_code == 200, r.text

                                                                            
    login(client, "alice")
    r = client.put("/api/v1/account/providers/openai_compatible", json={
        "enabled":True, "config":{"base_url":"https://api.example-alice.test/v1","model":"alice-model","qps":1,"max_concurrency":1},
        "secrets":{"api_key":"alice-secret-key"},
    })
    assert r.status_code == 200 and r.json()["configured"] is True, r.text
    r = client.post("/api/v1/account/providers", json={"kind":"openai_compatible","display_name":"Alice backup"})
    assert r.status_code == 200 and r.json()["custom"] is True, r.text
    alice_key = client.post("/api/v1/account/api-keys", json={"label":"Alice Zotero","scopes":["translate","lookup","download","account:read"]}).json()["api_key"]

                                                                 
    with SessionLocal() as db:
        alice = db.scalar(select(User).where(User.username == "alice"))
        job_id = uuid.uuid4().hex
        mono_key = f"results/{job_id}/mono.pdf"
        put_bytes(mono_key, TRANSLATED, "application/pdf")
        db.add(Job(
            id=job_id, filename="paper.pdf", status="COMPLETED", stage="completed", progress=100, stage_progress=100,
            lang_in="en", lang_out="zh-CN", pages=None, output_mode="mono", provider="openai_compatible",
            provider_ids=["openai_compatible"], provider_strategy="single", qps=1, pool_workers=1,
            user_id=alice.id, source_key=f"doi/{DOI}", source_sha256=None, document_doi=DOI,
            mono_key=mono_key, result_bytes=len(TRANSLATED), cache_hit=False,
        )); db.commit()

                                                                                  
    r = client.post("/api/v1/jobs/lookup", headers=api_auth(alice_key,"alice-desktop"), json={
        "document_doi":DOI,"lang_in":"en","lang_out":"zh-CN","output_mode":"mono",
        "filename":"paper.pdf","client_id":"alice-desktop","client_request_id":"alice-lookup",
    })
    assert r.status_code == 200 and r.json()["found"] is True and r.json()["job"]["id"] == job_id, r.text

                                                                                 
                                                 
    login(client, "bob")
    bob_key = client.post("/api/v1/account/api-keys", json={"label":"Bob Zotero","scopes":["translate","lookup","download","account:read"]}).json()["api_key"]
    r = client.post("/api/v1/jobs/lookup", headers=api_auth(bob_key,"bob-pc"), json={"document_doi":DOI,"lang_in":"en","lang_out":"zh-CN","output_mode":"mono"})
    assert r.status_code == 200 and r.json()["found"] is False, r.text

                                                                                  
                                                                                  
                                         
    r = client.post("/api/v1/jobs", headers=api_auth(bob_key,"bob-pc"),
        data={"document_doi":DOI,"lang_in":"en","lang_out":"zh-CN","output_mode":"mono","client_id":"bob-pc","client_request_id":"bob-upload"},
        files={"file":("paper.pdf",PDF,"application/pdf")})
    assert r.status_code == 200, r.text
    bob_job = r.json()
    assert bob_job["status"] == "COMPLETED" and bob_job["cache_hit"] is True and bob_job["source_sha256"] is None, bob_job
    res = client.get(f"/api/v1/jobs/{bob_job['id']}/result/mono", headers=api_auth(bob_key,"bob-pc"))
    assert res.status_code == 200 and res.content == TRANSLATED

                                                                          
    r = client.post("/api/v1/jobs/lookup", headers=api_auth(bob_key,"bob-laptop"), json={
        "document_doi":DOI,"lang_in":"en","lang_out":"zh-CN","output_mode":"mono","client_id":"bob-laptop","client_request_id":"bob-second-device",
    })
    assert r.status_code == 200 and r.json()["found"] is True, r.text

                                                                                    
    save = client.put("/api/v1/account/providers/openai_compatible", json={
        "enabled":True,"config":{"base_url":"https://api.example-bob.test/v1","model":"bob-model","qps":2,"max_concurrency":2},
        "secrets":{"api_key":"bob-secret-key"},
    })
    assert save.status_code == 200 and save.json()["configured"] is True
    custom = client.post("/api/v1/account/providers", json={"kind":"openai_compatible","display_name":"Bob backup"}).json()
    r = client.put(f"/api/v1/account/providers/{custom['id']}", json={
        "enabled":True,"config":{"base_url":"https://api.backup-bob.test/v1","model":"backup-model"},"secrets":{"api_key":"bob-backup-key"},
    })
    assert r.status_code == 200 and r.json()["configured"] is True
    defaults = client.put("/api/v1/account/providers/settings/default", json={"default_provider_ids":["openai_compatible",custom["id"]],"default_provider_strategy":"failover"})
    assert defaults.status_code == 200 and defaults.json()["default_provider_strategy"] == "failover", defaults.text

    with SessionLocal() as db:
        bob = db.scalar(select(User).where(User.username == "bob"))
        alice = db.scalar(select(User).where(User.username == "alice"))
        bob_row = db.scalar(select(UserProviderProfile).where(UserProviderProfile.user_id == bob.id, UserProviderProfile.provider_id == "openai_compatible"))
        alice_row = db.scalar(select(UserProviderProfile).where(UserProviderProfile.user_id == alice.id, UserProviderProfile.provider_id == "openai_compatible"))
        assert bob_row.config["base_url"] == "https://api.example-bob.test/v1"
        assert alice_row.config["base_url"] == "https://api.example-alice.test/v1"
        assert bob_row.secret_payload != alice_row.secret_payload

                                                                            
    assert client.get("/api/v1/account/providers", headers=api_auth(bob_key,"bob-pc")).status_code == 403
    account = client.get("/api/v1/account/summary")
    assert account.status_code == 200 and account.json()["today"]["calls"] >= 2
    admin = client.get("/api/v1/admin/summary", headers=service)
    assert admin.status_code == 200 and admin.json()["total_users"] >= 4

                                                                                
                                                                        
    with SessionLocal() as db:
        bob_id = db.scalar(select(User.id).where(User.username == "bob"))
    r = client.patch(f"/api/v1/admin/users/{bob_id}", headers=service, json={"password":"new-password-67890"})
    assert r.status_code == 200
    assert client.get("/api/v1/account/summary").status_code == 401
    assert client.get("/api/v1/auth/client", headers=api_auth(bob_key,"bob-pc")).status_code == 200

                                          
    statuses=[]
    for _ in range(11):
        rr=client.post("/api/v1/auth/login",json={"username":"nonexistent-rate-test","password":"definitely-wrong","device_code":"rate-test","device_name":"rate-test"})
        statuses.append(rr.status_code)
    assert statuses[:10] == [401]*10 and statuses[-1] == 429, statuses

print("accounts-devices-cache-smoke: ok")
