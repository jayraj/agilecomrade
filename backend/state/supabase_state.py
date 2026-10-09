"""Supabase-backed StateBackend: shared rates, locks, and TTL caches.

State is an optimization, not a source of truth, so every operation fails open
(allow / miss) when PostgREST or the database is unavailable — a state outage
must never take down a user request.
"""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote as urlquote

import requests

from config import settings

logger = logging.getLogger(__name__)

TABLE = "kv_store"
_TIMEOUT = 5
_LOCK_POLL_SECONDS = 0.2
_LOCK_MAX_WAIT = 30  # cap the blocking wait so a down backend can't hang a request


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _parse_ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class _SupabaseLock:
    """Named lock backed by the kv_acquire_lock RPC (TTL auto-expires)."""

    def __init__(self, client: "SupabaseStateBackend", name: str, ttl_seconds: int):
        self._client = client
        self._name = name
        self._ttl = ttl_seconds
        self._held = False

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if self._client.acquire_lock(self._name, self._ttl):
            self._held = True
            return True
        if not blocking:
            return False
        wait = _LOCK_MAX_WAIT if (timeout is None or timeout < 0) else timeout
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(_LOCK_POLL_SECONDS)
            if self._client.acquire_lock(self._name, self._ttl):
                self._held = True
                return True
        return False

    def release(self) -> None:
        if self._held:
            self._held = False
            self._client.release_lock(self._name)


class SupabaseStateBackend:
    def __init__(self, url: str = "", service_role_key: str = ""):
        self.base_url = (url or settings.supabase_url).rstrip("/")
        self.service_role_key = service_role_key or settings.supabase_service_role_key

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.service_role_key)

    def _headers(self, prefer: str = "") -> dict:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _rpc(self, fn: str, payload: dict):
        if not self.enabled:
            raise RuntimeError("Supabase state backend not configured")
        url = f"{self.base_url}/rest/v1/rpc/{fn}"
        response = requests.post(url, json=payload, headers=self._headers(), timeout=_TIMEOUT)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase state rpc {fn} failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json() if response.text else None

    def _request(self, method: str, path: str, json: dict | None = None, prefer: str = ""):
        if not self.enabled:
            raise RuntimeError("Supabase state backend not configured")
        url = f"{self.base_url}/rest/v1/{path}"
        response = requests.request(
            method, url, json=json, headers=self._headers(prefer), timeout=_TIMEOUT
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase state {method} {path} failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json() if response.text else []

    # ------------------------------------------------------------------ #
    # Rate limiting
    # ------------------------------------------------------------------ #
    def check_rate(self, key: str, max_requests: int, window_seconds: int) -> bool:
        try:
            count = self._rpc(
                "kv_incr",
                {"p_bucket": "rate", "p_key": key, "p_window_seconds": window_seconds},
            )
        except Exception as e:
            logger.warning(f"State rate check failed open: {e}")
            return True
        return count is None or int(count) <= max_requests

    # ------------------------------------------------------------------ #
    # Named locks
    # ------------------------------------------------------------------ #
    def acquire_lock(self, name: str, ttl_seconds: int) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(
                self._rpc(
                    "kv_acquire_lock",
                    {"p_bucket": "lock", "p_key": name, "p_ttl_seconds": ttl_seconds},
                )
            )
        except Exception as e:
            logger.warning(f"State lock acquire failed open: {e}")
            return False

    def release_lock(self, name: str) -> None:
        try:
            self._rpc("kv_release_lock", {"p_bucket": "lock", "p_key": name})
        except Exception as e:
            logger.warning(f"State lock release failed: {e}")

    def lock(self, name: str, ttl_seconds: int) -> _SupabaseLock:
        return _SupabaseLock(self, name, ttl_seconds)

    # ------------------------------------------------------------------ #
    # TTL cache
    # ------------------------------------------------------------------ #
    def cache_get(self, namespace: str, key: str):
        if not self.enabled:
            return None
        try:
            rows = self._request(
                "GET",
                f"{TABLE}?bucket=eq.{urlquote(namespace, safe='')}"
                f"&key=eq.{urlquote(key, safe='')}&select=value,expires_at",
            )
        except Exception as e:
            logger.warning(f"State cache get failed open: {e}")
            return None
        if not rows:
            return None
        expires = rows[0].get("expires_at")
        if expires and _parse_ts(expires) <= time.time():
            return None
        return rows[0].get("value")

    def cache_set(self, namespace: str, key: str, value, ttl_seconds: int) -> None:
        if not self.enabled:
            return
        expires = None
        if ttl_seconds > 0:
            expires = _iso(time.time() + ttl_seconds)
        try:
            self._request(
                "POST",
                f"{TABLE}?on_conflict=bucket,key",
                json={"bucket": namespace, "key": key, "value": value, "expires_at": expires},
                prefer="resolution=merge-duplicates",
            )
        except Exception as e:
            logger.warning(f"State cache set failed open: {e}")
