from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager


class GlobalRateGate:
    """Per-provider process-global QPS + concurrency gate with metrics.

    ZFT Cloud v1.3 can dispatch one BabelDOC document across several translation
    providers. BabelDOC therefore uses an aggregate QPS, while this gate enforces
    the hard limit for each individual provider.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.next_request: dict[str, float] = defaultdict(float)
        self.active: dict[str, int] = defaultdict(int)
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.errors: dict[str, deque[float]] = defaultdict(deque)
        self.daily: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"requests": 0, "characters": 0, "wait_ms": 0.0}
        )
        self.last_error: dict[str, dict] = {}

    def acquire(self, provider: str, qps: float, max_concurrency: int = 1) -> float:
        qps = max(0.1, float(qps))
        max_concurrency = max(1, int(max_concurrency))
        started = time.monotonic()

        # Reserve a concurrency slot first.
        with self.cond:
            while self.active[provider] >= max_concurrency:
                self.cond.wait(timeout=0.25)
            self.active[provider] += 1

        # Smooth request starts with a leaky-bucket interval.
        try:
            with self.lock:
                now = time.monotonic()
                scheduled = max(now, self.next_request[provider])
                self.next_request[provider] = scheduled + (1.0 / qps)
            delay = scheduled - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            return max(0.0, (time.monotonic() - started) * 1000.0)
        except Exception:
            self.release(provider)
            raise

    def release(self, provider: str) -> None:
        with self.cond:
            if self.active[provider] > 0:
                self.active[provider] -= 1
            self.cond.notify_all()

    @contextmanager
    def slot(self, provider: str, qps: float, max_concurrency: int = 1):
        wait_ms = self.acquire(provider, qps, max_concurrency)
        try:
            yield wait_ms
        finally:
            self.release(provider)

    def _trim(self, dq: deque[float], now: float, age: float = 3600.0):
        while dq and dq[0] < now - age:
            dq.popleft()

    def record_request(self, provider: str, *, chars: int = 0, wait_ms: float = 0.0) -> None:
        now = time.time()
        day = time.strftime("%Y%m%d", time.localtime(now))
        with self.lock:
            dq = self.requests[provider]
            dq.append(now)
            self._trim(dq, now)
            d = self.daily[(provider, day)]
            d["requests"] += 1
            d["characters"] += max(0, int(chars))
            d["wait_ms"] += max(0.0, float(wait_ms))

    def record_error(self, provider: str, message: str) -> None:
        now = time.time()
        with self.lock:
            dq = self.errors[provider]
            dq.append(now)
            self._trim(dq, now)
            self.last_error[provider] = {"at": int(now * 1000), "message": str(message)[:500]}

    def snapshot(self, provider: str, qps_limit: float) -> dict:
        now = time.time()
        day = time.strftime("%Y%m%d", time.localtime(now))
        with self.lock:
            req = self.requests[provider]
            err = self.errors[provider]
            self._trim(req, now)
            self._trim(err, now)
            req60 = sum(1 for t in req if t >= now - 60)
            err60 = sum(1 for t in err if t >= now - 60)
            daily = dict(self.daily[(provider, day)])
            last_error = self.last_error.get(provider)
            active = int(self.active.get(provider, 0))
        return {
            "provider": provider,
            "qps_limit": float(qps_limit),
            "requests_last_60s": req60,
            "effective_qps": round(req60 / 60.0, 3),
            "errors_last_60s": err60,
            "active_requests": active,
            "today_requests": int(daily["requests"]),
            "today_characters": int(daily["characters"]),
            "today_wait_ms": round(float(daily["wait_ms"]), 1),
            "last_error": last_error,
            "metrics_scope": "since-container-start",
        }


gate = GlobalRateGate()
