from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .events import cancel_requested, clear_cancel, publish
from .joblog import record_job_event
from .models import Job
from .runtime import get_runtime_snapshot
from .storage import download_to, upload_file
from .services.document_bindings import bind_completed_job

settings = get_settings()
_ACTIVE = {"PARSING", "TRANSLATING", "TYPESETTING", "RENDERING", "FINALIZING", "CANCELLING", "RUNNING"}
_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def now():
    return datetime.now(timezone.utc)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def update_job(job_id: str, **values):
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        for key, value in values.items():
            setattr(job, key, value)
        db.commit()


def emit(job_id: str, **payload):
    update = {k: payload[k] for k in ("status", "stage", "progress", "stage_progress") if k in payload}
    stage_meta = {k: payload.get(k) for k in ("stage_current", "stage_total") if payload.get(k) is not None}
    if stage_meta:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job:
                metrics = dict(job.metrics or {})
                metrics.update(stage_meta)
                job.metrics = metrics
                for key, value in update.items():
                    setattr(job, key, value)
                db.commit()
    elif update:
        update_job(job_id, **update)
    record_job_event(job_id, payload)
    publish(job_id, payload)


def process_job(job_id: str):
                                                                                
                                                                               
                                                                                
    from .services.babeldoc_adapter import run_babeldoc
    runtime = get_runtime_snapshot()
    worker_name = f"embedded:{socket.gethostname()}:{os.getpid()}"
    clear_cancel(job_id)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status in _TERMINAL:
            return
        source_key = job.source_key
        lang_in, lang_out, pages, output_mode = job.lang_in, job.lang_out, job.pages, job.output_mode
        provider_ids = list(job.provider_ids or []) or [job.provider]
        provider_strategy = job.provider_strategy or ("balanced" if len(provider_ids) > 1 else "single")
        user_id = job.user_id
        job_metrics = dict(job.metrics or {})
        ignore_cache = bool(job_metrics.get("ignore_cache") or job_metrics.get("force_retranslate"))
        job.started_at = now()
        job.finished_at = None
        job.status = "PARSING"
        job.stage = "preparing (cache bypass)" if ignore_cache else "preparing"
        job.worker_name = worker_name
        job.qps = runtime.babeldoc_qps
        job.pool_workers = runtime.multi_pool_max_workers if len(provider_ids) > 1 else runtime.pool_max_workers
        db.commit()

    emit(job_id, type="state", status="PARSING", stage=("preparing (cache bypass)" if ignore_cache else "preparing"), progress=1.0, stage_progress=0.0, worker=worker_name, ignore_cache=ignore_cache)
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"zft-{job_id}-", dir=str(settings.work_dir)))
    input_pdf = work_dir / "source.pdf"
    output_dir = work_dir / "output"
    try:
        download_to(source_key, input_pdf)
        if cancel_requested(job_id):
            raise asyncio.CancelledError()

        async def progress_cb(payload: dict):
            if payload.get("type") == "provider_pool":
                update_job(job_id, qps=int(payload.get("aggregate_qps") or 1), pool_workers=int(payload.get("translation_workers") or 1))
            emit(job_id, **payload)

        try:
            result = asyncio.run(run_babeldoc(
                job_id, input_pdf, output_dir, lang_in, lang_out, pages, output_mode,
                provider_ids, provider_strategy, runtime, user_id, progress_cb, ignore_cache=ignore_cache,
            ))
        except asyncio.CancelledError as exc:
            if cancel_requested(job_id):
                raise
            raise RuntimeError("BabelDOC internal CancelledError") from exc
        except RuntimeError as exc:
            if "CancelledError" not in str(exc) or cancel_requested(job_id):
                raise
                                                                                     
                                                                                    
                                                                                  
                                                                          
            emit(job_id, type="state", status="PARSING", stage="retrying BabelDOC", progress=2.0, stage_progress=0.0)
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            try:
                result = asyncio.run(run_babeldoc(
                    job_id, input_pdf, output_dir, lang_in, lang_out, pages, output_mode,
                    provider_ids, provider_strategy, runtime, user_id, progress_cb, disable_split=True, ignore_cache=ignore_cache,
                ))
            except asyncio.CancelledError as retry_exc:
                if cancel_requested(job_id):
                    raise
                raise RuntimeError("BabelDOC internal CancelledError after no-split retry") from retry_exc
        mono = getattr(result, "no_watermark_mono_pdf_path", None) or getattr(result, "mono_pdf_path", None)
        dual = getattr(result, "no_watermark_dual_pdf_path", None) or getattr(result, "dual_pdf_path", None)
        values = {}
        result_bytes = 0
        if mono:
            mono_path = Path(mono)
            mono_key = f"results/{job_id}/mono.pdf"
            upload_file(mono_key, mono_path)
            values["mono_key"] = mono_key
            try:
                values["mono_sha256"] = sha256_file(mono_path)
            except Exception:
                values["mono_sha256"] = None
            try: result_bytes += int(mono_path.stat().st_size)
            except Exception: pass
        if dual:
            dual_path = Path(dual)
            dual_key = f"results/{job_id}/dual.pdf"
            upload_file(dual_key, dual_path)
            values["dual_key"] = dual_key
            try:
                values["dual_sha256"] = sha256_file(dual_path)
            except Exception:
                values["dual_sha256"] = None
            try: result_bytes += int(dual_path.stat().st_size)
            except Exception: pass
        values["result_bytes"] = result_bytes
        metrics = {
            **job_metrics,
            "cache_bypass": ignore_cache,
            "total_seconds": getattr(result, "total_seconds", None),
            "peak_memory_usage": getattr(result, "peak_memory_usage", None),
            "total_valid_character_count": getattr(result, "total_valid_character_count", None),
            "total_valid_text_token_count": getattr(result, "total_valid_text_token_count", None),
            "provider_ids": provider_ids,
            "provider_strategy": provider_strategy,
        }
                                                                            
                                                                                
                                                                                
                                                                            
        with SessionLocal() as db:
            completed = db.get(Job, job_id)
            if completed is None:
                raise RuntimeError("job disappeared before completion commit")
            completed.status = "COMPLETED"
            completed.stage = "completed"
            completed.progress = 100.0
            completed.stage_progress = 100.0
            completed.finished_at = now()
            completed.metrics = metrics
            for key, value in values.items():
                setattr(completed, key, value)
            bind_completed_job(db, completed, commit=False)
            db.commit()
        payload = {"type": "finish", "status": "COMPLETED", "stage": "completed", "progress": 100.0, "files": {"mono": bool(mono), "dual": bool(dual)}, "metrics": metrics}
        record_job_event(job_id, payload)
        publish(job_id, payload)
    except asyncio.CancelledError:
        update_job(job_id, status="CANCELLED", stage="cancelled", finished_at=now())
        payload = {"type": "state", "status": "CANCELLED", "stage": "cancelled"}
        record_job_event(job_id, payload)
        publish(job_id, payload)
    except Exception as exc:
        update_job(job_id, status="FAILED", stage="failed", finished_at=now(), error_code=exc.__class__.__name__, error_message=str(exc)[:2000])
        payload = {"type": "error", "status": "FAILED", "stage": "failed", "error_code": exc.__class__.__name__, "error_message": str(exc)[:2000]}
        record_job_event(job_id, payload)
        publish(job_id, payload)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class EmbeddedTaskManager:
    def __init__(self):
        self.started_at = time.monotonic()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    def start(self):
        if self._scheduler and self._scheduler.is_alive():
            return
                                                                                 
        with SessionLocal() as db:
            rows = db.scalars(select(Job).where(Job.status.in_(_ACTIVE))).all()
            for job in rows:
                job.status = "QUEUED"
                job.stage = "recovered after restart"
                job.worker_name = None
            db.commit()
        self._stop.clear()
        self._scheduler = threading.Thread(target=self._run, name="zft-embedded-scheduler", daemon=True)
        self._scheduler.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._scheduler:
            self._scheduler.join(timeout=3)

    def submit(self, job_id: str):
        clear_cancel(job_id)
        self._wake.set()

    def _cleanup(self):
        with self._lock:
            done = [job_id for job_id, thread in self._threads.items() if not thread.is_alive()]
            for job_id in done:
                self._threads.pop(job_id, None)

    def _launch(self, job_id: str):
        def target():
            try:
                process_job(job_id)
            finally:
                self._wake.set()
        thread = threading.Thread(target=target, name=f"zft-job-{job_id[:8]}", daemon=True)
        with self._lock:
            self._threads[job_id] = thread
        thread.start()

    def _run(self):
        while not self._stop.is_set():
            self._cleanup()
            try:
                runtime = get_runtime_snapshot()
                with self._lock:
                    active = len(self._threads)
                capacity = max(0, runtime.max_active_jobs - active)
                if capacity:
                    with SessionLocal() as db:
                        jobs = db.scalars(select(Job).where(Job.status == "QUEUED").order_by(Job.created_at.asc()).limit(capacity)).all()
                        ids = [j.id for j in jobs]
                    for job_id in ids:
                        self._launch(job_id)
            except Exception:
                                                                                         
                pass
            self._wake.wait(0.75)
            self._wake.clear()

    def snapshot(self) -> dict:
        self._cleanup()
        with self._lock:
            active = len(self._threads)
            names = list(self._threads)
        with SessionLocal() as db:
            queued = len(db.scalars(select(Job).where(Job.status == "QUEUED")).all())
        return {
            "name": "embedded-babeldoc-worker",
            "active": active,
            "queued": queued,
            "job_ids": names,
            "uptime": int(time.monotonic() - self.started_at),
            "pid": os.getpid(),
        }


manager = EmbeddedTaskManager()
