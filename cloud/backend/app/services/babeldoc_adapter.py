from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from babeldoc.format.pdf.high_level import async_translate
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.translator.translator import set_translate_rate_limiter

from ..events import cancel_requested
from ..runtime import RuntimeSnapshot
from .providers import create_multi_translator


STAGE_MAP = {
    "Parse PDF and Create Intermediate Representation": "PARSING",
    "DetectScannedFile": "PARSING",
    "Parse Page Layout": "PARSING",
    "Parse Table": "PARSING",
    "Parse Paragraphs": "PARSING",
    "Parse Formulas and Styles": "PARSING",
    "Extract Terms": "PARSING",
    "Translate Paragraphs": "TRANSLATING",
    "Typesetting": "TYPESETTING",
    "Add Fonts": "TYPESETTING",
    "Generate drawing instructions": "RENDERING",
    "Subset font": "RENDERING",
    "Save PDF": "FINALIZING",
}


def _state_from_stage(stage: str) -> str:
    low = (stage or "").lower()
    for key, value in STAGE_MAP.items():
        if key.lower() in low or (low and low in key.lower()):
            return value
    if "translate" in low:
        return "TRANSLATING"
    if "layout" in low or "parse" in low:
        return "PARSING"
    if "type" in low or "font" in low:
        return "TYPESETTING"
    if "pdf" in low or "save" in low:
        return "RENDERING"
    return "RUNNING"


async def run_babeldoc(
    job_id: str,
    input_pdf: Path,
    output_dir: Path,
    lang_in: str,
    lang_out: str,
    pages: str | None,
    output_mode: str,
    provider_ids: list[str],
    provider_strategy: str,
    runtime: RuntimeSnapshot,
    user_id: str | None,
    progress_cb,
    disable_split: bool = False,
    ignore_cache: bool = False,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    translator, provider_qps_sum, provider_concurrency_sum = create_multi_translator(
        provider_ids, lang_in, lang_out, strategy=provider_strategy,
        quota_aware=runtime.quota_aware_dispatch, ignore_cache=ignore_cache, user_id=user_id
    )
    multi = len(provider_ids) > 1
    qps = max(1, min(runtime.aggregate_qps_cap, int(round(provider_qps_sum))))
    worker_cap = runtime.multi_pool_max_workers if multi else runtime.pool_max_workers
    workers = max(1, min(worker_cap, provider_concurrency_sum, qps))

                                                                                     
                                                                                    
                                                                                
    set_translate_rate_limiter(qps)

    await progress_cb({
        "type": "provider_pool",
        "status": "PARSING",
        "stage": "translation provider pool ready",
        "provider_ids": provider_ids,
        "provider_strategy": provider_strategy,
        "aggregate_qps": qps,
        "translation_workers": workers,
        "ignore_cache": bool(ignore_cache),
    })

    config = TranslationConfig(
        input_file=str(input_pdf),
        output_dir=str(output_dir),
        working_dir=str(output_dir / "work"),
        translator=translator,
        doc_layout_model=None,
        lang_in=lang_in,
        lang_out=lang_out,
        pages=pages,
        no_dual=(output_mode == "mono"),
        no_mono=(output_mode == "dual"),
        qps=qps,
        pool_max_workers=workers,
        report_interval=runtime.report_interval,
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        skip_scanned_detection=runtime.skip_scanned_detection,
        auto_enable_ocr_workaround=runtime.auto_ocr_workaround,
        auto_extract_glossary=False,
        save_auto_extracted_glossary=False,
        split_strategy=None if disable_split else TranslationConfig.create_max_pages_per_part_split_strategy(runtime.max_pages_per_part),
    )

    async def watch_cancel():
        while True:
            if cancel_requested(job_id):
                config.cancel_translation()
                return
            await asyncio.sleep(0.35)

    cancel_task = asyncio.create_task(watch_cancel())
    result = None
    try:
        async for event in async_translate(config):
            if cancel_requested(job_id):
                config.cancel_translation()
            etype = event.get("type")
            if etype in {"progress_start", "progress_update", "progress_end"}:
                stage = str(event.get("stage") or "running")
                payload = {
                    "type": "progress",
                    "status": _state_from_stage(stage),
                    "stage": stage,
                    "stage_progress": float(event.get("stage_progress") or 0.0),
                    "stage_current": event.get("stage_current"),
                    "stage_total": event.get("stage_total"),
                }
                if event.get("overall_progress") is not None:
                    payload["progress"] = float(event["overall_progress"])
                await progress_cb(payload)
            elif etype == "error":
                message = str(event.get("error") or "BabelDOC failed")
                if "CancelledError" in message and not cancel_requested(job_id):
                    raise RuntimeError("BabelDOC internal CancelledError")
                raise RuntimeError(message)
            elif etype == "finish":
                result = event.get("translate_result")
                break
        if result is None:
            if cancel_requested(job_id):
                raise asyncio.CancelledError()
            raise RuntimeError("BabelDOC ended without a finish result")
        return result
    finally:
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
