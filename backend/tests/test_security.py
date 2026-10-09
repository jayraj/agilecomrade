import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from starlette.requests import Request

import main as app
from state.memory import _RATE_MAX_KEYS, MemoryStateBackend


def _request(headers: dict, client=("10.0.0.1", 12345)) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_client_ip_uses_forwarded_for() -> None:
    assert app._client_ip(_request({"x-forwarded-for": "203.0.113.9, 70.41.3.18"})) == "203.0.113.9"


def test_client_ip_falls_back_to_real_ip_then_peer() -> None:
    assert app._client_ip(_request({"x-real-ip": "198.51.100.7"})) == "198.51.100.7"
    assert app._client_ip(_request({})) == "10.0.0.1"


def test_rate_limit_blocks_after_max() -> None:
    key = f"security-test-rate-{time.monotonic()}"
    assert app.rate_limit(key, 2, 60) is None
    assert app.rate_limit(key, 2, 60) is None
    blocked = app.rate_limit(key, 2, 60)
    assert blocked is not None
    assert blocked.status_code == 429


def test_rate_bucket_map_is_bounded() -> None:
    backend = MemoryStateBackend()
    stale = time.monotonic() - 10_000
    for i in range(_RATE_MAX_KEYS + 5):
        backend._rate_buckets[f"stale-{i}"] = (stale, 1)

    backend.check_rate("fresh-key", 1, 60)

    assert len(backend._rate_buckets) <= _RATE_MAX_KEYS


def test_api_responses_have_security_headers_and_no_store() -> None:
    client = TestClient(app.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert response.headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()"
    assert response.headers["cache-control"] == "no-store"


def test_non_api_response_is_not_marked_no_store() -> None:
    client = TestClient(app.app)
    response = client.get("/")
    assert response.status_code == 200
    assert "cache-control" not in response.headers


def test_oversized_request_body_is_rejected() -> None:
    client = TestClient(app.app)
    response = client.post("/api/test-config", content=b"x" * (app._MAX_BODY_BYTES + 1))
    assert response.status_code == 413