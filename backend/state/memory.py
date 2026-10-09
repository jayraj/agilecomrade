"""Per-process StateBackend: fixed-window rates, named locks, TTL cache."""

import threading
import time

# Bound the rate-limit map so a flood of distinct keys (e.g. spoofed IPs)
# can't grow it without limit.
_RATE_MAX_KEYS = 10_000
_RATE_MAX_WINDOW = 3600


class _MemoryLock:
    """Named lock wrapping a ``threading.Lock`` (TTL is a no-op in-process)."""

    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        return self._lock.acquire(blocking)

    def release(self) -> None:
        self._lock.release()


class MemoryStateBackend:
    def __init__(self):
        self._rate_buckets: dict[str, tuple[float, int]] = {}
        self._rate_lock = threading.Lock()
        self._locks: dict[str, _MemoryLock] = {}
        self._locks_guard = threading.Lock()
        self._cache: dict[tuple[str, str], tuple[object, float | None]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Rate limiting (fixed window, matching the Supabase kv_incr RPC)
    # ------------------------------------------------------------------ #
    def check_rate(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._rate_lock:
            start, count = self._rate_buckets.get(key, (now, 0))
            if now - start >= window_seconds:
                start, count = now, 0
            count += 1
            self._rate_buckets[key] = (start, count)
            if len(self._rate_buckets) > _RATE_MAX_KEYS:
                self._prune(now)
            return count <= max_requests

    def _prune(self, now: float) -> None:
        for key, (start, _count) in list(self._rate_buckets.items()):
            if now - start > _RATE_MAX_WINDOW:
                self._rate_buckets.pop(key, None)

    # ------------------------------------------------------------------ #
    # Named locks
    # ------------------------------------------------------------------ #
    def lock(self, name: str, ttl_seconds: int) -> _MemoryLock:
        with self._locks_guard:
            return self._locks.setdefault(name, _MemoryLock())

    # ------------------------------------------------------------------ #
    # TTL cache
    # ------------------------------------------------------------------ #
    def cache_get(self, namespace: str, key: str):
        now = time.monotonic()
        with self._cache_lock:
            item = self._cache.get((namespace, key))
            if item is None:
                return None
            value, expires = item
            if expires is not None and now >= expires:
                self._cache.pop((namespace, key), None)
                return None
            return value

    def cache_set(self, namespace: str, key: str, value, ttl_seconds: int) -> None:
        expires = None if ttl_seconds <= 0 else time.monotonic() + ttl_seconds
        with self._cache_lock:
            self._cache[(namespace, key)] = (value, expires)
