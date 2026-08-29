"""Cloud 2.3: HttpOnly web session + API-key lifecycle + DOI document lock."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="zft-v23-key-lock-"))
os.environ["DATABASE_URL"] = f"sqlite:///{root / 'test.db'}"
os.environ["ZFT_DATA_DIR"] = str(root)
os.environ["ZFT_STORAGE_DIR"] = str(root / "files")
os.environ["ZFT_WORK_DIR"] = str(root / "work")
os.environ["ZFT_STATIC_DIR"] = str(root / "user")
os.environ["ZFT_ADMIN_STATIC_DIR"] = str(root / "admin")
os.environ["ZFT_API_KEY"] = "service-test-key"
os.environ["ZFT_CONFIG_SECRET"] = "test-config-secret-v23"
os.environ["ZFT_BOOTSTRAP_ADMIN_PASSWORD"] = ""

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.main import app
from app.models import ClientApiKey, Device, Job, TranslationVersion, User, UserDocumentBinding
from app.services.document_bindings import bind_completed_job
from app.storage import put_bytes

OLD = b"%PDF-1.4\ntranslated-old\n%%EOF\n"
NEW = b"%PDF-1.4\ntranslated-new\n%%EOF\n"
DOI = "10.1234/example.paper.2026"


def bearer(token: str, device: str | None = None):
    h = {"Authorization": f"Bearer {token}"}
    if device:
        h.update({"X-ZFT-Device-ID": device, "X-ZFT-Platform": "test", "X-ZFT-Client-Version": "0.3.7"})
    return h


with TestClient(app) as client:
                                                                              
                                                             
    r = client.post("/api/v1/auth/register", json={
        "username": "owner", "password": "password-12345",
        "device_code": "browser-session", "device_name": "Browser",
    })
    assert r.status_code == 200, r.text
    assert "token" not in r.json(), r.json()
    assert "zft_user_session=" in (r.headers.get("set-cookie") or "")
    assert "httponly" in (r.headers.get("set-cookie") or "").lower()

                                                                             
    r = client.post("/api/v1/account/api-keys", json={
        "label": "Zotero", "scopes": ["translate", "lookup", "download", "account:read"], "expires_in_days": 30,
    })
    assert r.status_code == 200, r.text
    raw_key = r.json()["api_key"]
    key_id = r.json()["id"]
    old_expiry = r.json()["expires_at"]
    assert raw_key.startswith("zftk_") and r.json()["key_prefix"] == raw_key[:16]
    with SessionLocal() as db:
        row = db.get(ClientApiKey, key_id)
        assert row is not None and row.key_hash != raw_key and raw_key not in row.key_hash
        assert set(row.scopes) == {"translate", "lookup", "download", "account:read"}

                                                                          
    for device in ("device-uuid-a-0001", "device-uuid-b-0002"):
        r = client.get("/api/v1/auth/client", headers=bearer(raw_key, device))
        assert r.status_code == 200, r.text
        assert r.json()["user"]["username"] == "owner"
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "owner"))
        devices = db.scalars(select(Device).where(Device.user_id == owner.id)).all()
        assert {d.device_code for d in devices} >= {"device-uuid-a-0001", "device-uuid-b-0002"}

                                                                           
        old_id = uuid.uuid4().hex
        put_bytes(f"results/{old_id}/mono.pdf", OLD, "application/pdf")
        old_job = Job(
            id=old_id, filename="paper.pdf", status="COMPLETED", stage="completed",
            progress=100, stage_progress=100, lang_in="en", lang_out="zh-CN", pages=None,
            output_mode="mono", provider="openai_compatible", provider_ids=["openai_compatible"],
            provider_strategy="single", qps=1, pool_workers=1, user_id=owner.id,
            source_key=f"doi/{DOI}", source_sha256=None, document_doi=DOI,
            mono_key=f"results/{old_id}/mono.pdf", result_bytes=len(OLD), cache_hit=False,
        )
        db.add(old_job); db.commit()

                                                                                
                                                                         
    lookup_payload = {"document_doi": DOI, "lang_in": "en", "lang_out": "zh-CN", "output_mode": "mono", "filename": "paper.pdf"}
    r = client.post("/api/v1/jobs/lookup", headers=bearer(raw_key, "device-uuid-a-0001"), json={**lookup_payload, "client_id":"a", "client_request_id":"lookup-a"})
    assert r.status_code == 200 and r.json()["found"] is True, r.text
    assert r.json()["job"]["mono_sha256"] == hashlib.sha256(OLD).hexdigest()

    r = client.post("/api/v1/jobs/lookup", headers=bearer(raw_key, "device-uuid-b-0002"), json={**lookup_payload, "client_id":"b", "client_request_id":"lookup-b"})
    assert r.status_code == 200 and r.json()["match"] == "account-document-doi-lock", r.text
    locked_b = r.json()["job"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TranslationVersion)) == 1
    res = client.get(f"/api/v1/jobs/{locked_b['id']}/result/mono", headers=bearer(raw_key, "device-uuid-b-0002"))
    assert res.status_code == 200 and res.content == OLD
    assert res.headers.get("x-zft-result-sha256") == hashlib.sha256(OLD).hexdigest()

                                                                                   
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "owner"))
        new_id = uuid.uuid4().hex
        put_bytes(f"results/{new_id}/mono.pdf", NEW, "application/pdf")
        new_job = Job(
            id=new_id, filename="paper.pdf", status="COMPLETED", stage="completed",
            progress=100, stage_progress=100, lang_in="en", lang_out="zh-CN", pages=None,
            output_mode="mono", provider="openai_compatible", provider_ids=["openai_compatible"],
            provider_strategy="single", qps=1, pool_workers=1, user_id=owner.id,
            source_key=f"doi/{DOI}", source_sha256=None, document_doi=DOI,
            mono_key=f"results/{new_id}/mono.pdf", result_bytes=len(NEW), cache_hit=False,
            metrics={"force_retranslate": True},
        )
        db.add(new_job); db.flush(); bind_completed_job(db, new_job, commit=False); db.commit()
        binding = db.scalar(select(UserDocumentBinding).where(UserDocumentBinding.user_id == owner.id, UserDocumentBinding.document_doi == DOI))
        assert binding is not None and binding.bound_job_id == new_id and binding.current_version_id
        version = db.get(TranslationVersion, binding.current_version_id)
        assert version is not None and version.job_id == new_id

    r = client.post("/api/v1/jobs/lookup", headers=bearer(raw_key, "device-uuid-a-0001"), json={**lookup_payload, "client_id":"a", "client_request_id":"lookup-a-v2"})
    assert r.status_code == 200 and r.json()["job"]["mono_sha256"] == hashlib.sha256(NEW).hexdigest(), r.text

                                                         
    r = client.post("/api/v1/account/api-keys", json={"label":"lookup-only", "scopes":["lookup"], "expires_in_days":7})
    lookup_only = r.json()["api_key"]
    assert client.get("/api/v1/auth/client", headers=bearer(lookup_only, "lookup-device")).status_code == 403
    assert client.post("/api/v1/jobs/lookup", headers=bearer(lookup_only, "lookup-device"), json=lookup_payload).status_code == 200
    upload = client.post("/api/v1/jobs", headers=bearer(lookup_only, "lookup-device"), data={"document_doi": DOI}, files={"file": ("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")})
    assert upload.status_code == 403 and "translate" in upload.text

                                                                                      
    r = client.post(f"/api/v1/account/api-keys/{key_id}/rotate", json={"expires_in_days": None})
    assert r.status_code == 200, r.text
    rotated = r.json()["api_key"]
    assert r.json()["rotated_from_id"] == key_id and r.json()["expires_at"] == old_expiry
    assert client.get("/api/v1/auth/client", headers=bearer(raw_key, "device-uuid-b-0002")).status_code == 401
    assert client.get("/api/v1/auth/client", headers=bearer(rotated, "device-uuid-b-0002")).status_code == 200

                                                                               
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "owner"))
        device_a = db.scalar(select(Device).where(Device.user_id == owner.id, Device.device_code == "device-uuid-a-0001"))
        device_a_id = device_a.id
    r = client.delete(f"/api/v1/account/devices/{device_a_id}")
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/client", headers=bearer(rotated, "device-uuid-a-0001")).status_code == 401
    assert client.get("/api/v1/auth/client", headers=bearer(rotated, "device-uuid-b-0002")).status_code == 200

                                                                     
    with SessionLocal() as db:
        assert db.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert str(db.execute(text("PRAGMA journal_mode")).scalar_one()).lower() == "wal"
        assert int(db.execute(text("PRAGMA busy_timeout")).scalar_one()) >= 30000
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_cloud_23"

print("client-api-key-document-lock-smoke: ok")
