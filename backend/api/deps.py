"""Shared request dependencies: error envelope, client IP, rate limiting, auth."""

import hmac
import logging
import threading
import time
from urllib.parse import quote as urlquote

from fastapi import Request
from fastapi.responses import JSONResponse

from crypto import sha256_hex
from services import store
from validation import validate_slug

logger = logging.getLogger(__name__)

# Reject oversized request bodies before a route buffers them. This is a cheap
# Content-Length guard; chunked bodies without a length are not covered.
_MAX_BODY_BYTES = 1_000_000

# Simple in-memory rate limiter (per key: list of timestamps). The key map is
# capped and pruned so a flood of distinct keys (e.g. spoofed IPs) can't grow it
# without bound.
_RATE_BUCKETS: dict = {}
_RATE_LOCK = threading.Lock()
_RATE_MAX_KEYS = 10_000
_RATE_MAX_WINDOW = 3600


def _prune_rate_buckets(now: float) -> None:
    for key in [k for k, ts in _RATE_BUCKETS.items() if not ts or now - max(ts) > _RATE_MAX_WINDOW]:
        _RATE_BUCKETS.pop(key, None)


def rate_limit(key: str, max_requests: int, window_seconds: int):
    """Returns None if allowed, or a JSONResponse with 429 if over the limit."""
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
        if len(bucket) >= max_requests:
            _RATE_BUCKETS[key] = bucket
            return _error("Too many requests. Please try again later.", 429)
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket
        if len(_RATE_BUCKETS) > _RATE_MAX_KEYS:
            _prune_rate_buckets(now)
    return None


def _error(message: str, status_code: int = 400) -> JSONResponse:
    """Uniform error envelope: {"status": "error", "error": message}."""
    return JSONResponse({"status": "error", "error": message}, status_code=status_code)


def _client_ip(request: Request) -> str:
    """Real client IP, honouring the proxy headers Vercel sets.

    Falling back to request.client.host would bucket every user under the
    proxy's IP, making IP-based rate limits useless in production.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    return request.client.host if request.client else "unknown"


def _auth(request: Request):
    """Resolve the profile from X-SRR-Profile + X-SRR-Token headers.

    Returns (profile_row, None) on success or (None, JSONResponse) on failure.
    Always responds 401 on failure so invalid slugs cannot be enumerated.
    """
    slug = (request.headers.get("X-SRR-Profile") or "").strip()
    token = (request.headers.get("X-SRR-Token") or "").strip()
    if not slug or not token or len(token) < 16 or not validate_slug(slug):
        return None, _error("Invalid credentials", 401)
    try:
        row = store.get_profile(urlquote(slug, safe=""))
    except Exception as e:
        logger.error(f"Supabase lookup failed for {slug}: {e}")
        return None, _error("Storage unavailable", 503)

    if not row or not hmac.compare_digest(row.get("access_token_hash", ""), sha256_hex(token)):
        return None, _error("Invalid credentials", 401)

    return row, None
