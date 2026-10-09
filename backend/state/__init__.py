"""Cross-instance application state: rate limits, refresh locks, TTL caches.

The default backend is per-process (`memory`). Set ``SRR_STATE_BACKEND=supabase``
to share state across serverless instances via the ``kv_store`` table so rate
limits and refresh locks hold process-wide. Every backend fails open: a state
error degrades to "allow / miss" rather than failing the user's request.
"""

from typing import Protocol

from config import settings


class StateLock(Protocol):
    """Minimal lock surface used by the snapshot refresh orchestration."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...
    def release(self) -> None: ...


class StateBackend(Protocol):
    def check_rate(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Record a hit for ``key``; return True when still within the limit."""
        ...

    def lock(self, name: str, ttl_seconds: int) -> StateLock:
        """Return a named lock that auto-expires after ``ttl_seconds``."""
        ...

    def cache_get(self, namespace: str, key: str):
        """Return the cached value, or None on miss/expiry."""
        ...

    def cache_set(self, namespace: str, key: str, value, ttl_seconds: int) -> None:
        """Store ``value``; ``ttl_seconds <= 0`` means no expiry."""
        ...


_backend: StateBackend | None = None


def get_backend() -> StateBackend:
    global _backend
    if _backend is None:
        if settings.state_backend.strip().lower() == "supabase":
            from state.supabase_state import SupabaseStateBackend

            _backend = SupabaseStateBackend()
        else:
            from state.memory import MemoryStateBackend

            _backend = MemoryStateBackend()
    return _backend


def reset_backend() -> None:
    """Testing hook: drop the cached backend so the next call re-selects."""
    global _backend
    _backend = None
