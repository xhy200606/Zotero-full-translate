from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict

_lock = threading.RLock()
_cancel_events: dict[str, threading.Event] = {}
_subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(list)
_last: dict[str, str] = {}


def _cancel_event(job_id: str) -> threading.Event:
    with _lock:
        return _cancel_events.setdefault(job_id, threading.Event())


def publish(job_id: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False, default=str)
    with _lock:
        _last[job_id] = body
        targets = list(_subscribers.get(job_id, []))
    for loop, queue in targets:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, body)
        except (RuntimeError, asyncio.QueueFull):
            pass


def request_cancel(job_id: str):
    _cancel_event(job_id).set()
    publish(job_id, {"type": "state", "status": "CANCELLING", "stage": "cancelling"})


def clear_cancel(job_id: str):
    _cancel_event(job_id).clear()


def cancel_requested(job_id: str) -> bool:
    return _cancel_event(job_id).is_set()


async def subscribe(job_id: str):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    target = (loop, queue)
    with _lock:
        _subscribers[job_id].append(target)
        last = _last.get(job_id)
    try:
        if last:
            yield last
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield None
    finally:
        with _lock:
            rows = _subscribers.get(job_id, [])
            if target in rows:
                rows.remove(target)
            if not rows:
                _subscribers.pop(job_id, None)
