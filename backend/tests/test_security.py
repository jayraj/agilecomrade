import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from starlette.requests import Request

import main as app


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
    key = "security-test-rate"
    app._RATE_BUCKETS.pop(key, None)
    assert app.rate_limit(key, 2, 60) is None
    assert app.rate_limit(key, 2, 60) is None
    blocked = app.rate_limit(key, 2, 60)
    assert blocked is not None
    assert blocked.status_code == 429


def test_rate_bucket_map_is_bounded() -> None:
    app._RATE_BUCKETS.clear()
    stale = time.monotonic() - 10_000
    for i in range(app._RATE_MAX_KEYS + 5):
        app._RATE_BUCKETS[f"stale-{i}"] = [stale]

    app.rate_limit("fresh-key", 1, 60)

    assert len(app._RATE_BUCKETS) <= app._RATE_MAX_KEYS


def test_api_responses_have_security_headers_and_no_store() -> None:
    client = TestClient(app.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
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