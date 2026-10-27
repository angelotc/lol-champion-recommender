"""Shared HTTP fetcher: one client, a polite rate limit, and de-duplication of identical
in-flight requests (prefetch and on-demand lookups never fetch the same thing twice)."""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterator

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) league-analysis pick helper"}


class RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)


class Fetcher:
    def __init__(self, per_second: float = 8.0, workers: int = 6, timeout: float = 10.0):
        self.http = httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True)
        self.limit = RateLimiter(per_second)
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="fetch")
        self._inflight: dict[str, Future] = {}
        self._lock = threading.Lock()
        self.failures = 0  # requests that failed for good; callers compare before/after a batch

    def note_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def once(self, key: str, fn: Callable[[], Any]) -> Any:
        """Run fn for this key, or wait for the call already running for it."""
        with self._lock:
            fut = self._inflight.get(key)
            owner = fut is None
            if owner:
                fut = self._inflight[key] = Future()
        if not owner:
            return fut.result()
        try:
            result = fn()
            fut.set_result(result)
            return result
        except BaseException as e:
            fut.set_exception(e)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def get(self, url: str, params: dict | None = None, attempts: int = 3) -> httpx.Response:
        """GET with retries on rate limits, server errors and dropped connections."""
        for i in range(1, attempts + 1):
            self.limit.wait()
            try:
                r = self.http.get(url, params=params)
            except httpx.TransportError:
                if i == attempts:
                    raise
                time.sleep(2.0 ** i)
                continue
            if r.status_code not in RETRY_STATUS or i == attempts:
                return r
            try:
                wait = min(float(r.headers.get("Retry-After", "")), 10.0)
            except ValueError:
                wait = 2.0 ** i
            time.sleep(wait)
        raise AssertionError("unreachable")

    def map(self, fn: Callable, items) -> Iterator:
        """fn over items on the shared pool; yields results in order as they finish.
        Don't call from inside a pool task (nested waits on a full pool deadlock)."""
        return self.pool.map(fn, items)

    def submit(self, fn: Callable, *args) -> Future:
        return self.pool.submit(fn, *args)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.http.close()
