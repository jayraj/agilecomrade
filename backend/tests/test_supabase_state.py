import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.supabase_state import SupabaseStateBackend, _iso


class _Resp:
    def __init__(self, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def _backend() -> SupabaseStateBackend:
    return SupabaseStateBackend(url="https://example.supabase.co", service_role_key="svc")


def test_check_rate_compares_count_to_limit(monkeypatch) -> None:
    monkeypatch.setattr("state.supabase_state.requests.post", lambda *a, **k: _Resp(200, "3"))
    backend = _backend()
    assert backend.check_rate("k", 5, 60) is True
    assert backend.check_rate("k", 3, 60) is True
    assert backend.check_rate("k", 2, 60) is False


def test_check_rate_fails_open_on_error(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("state.supabase_state.requests.post", boom)
    assert _backend().check_rate("k", 1, 60) is True


def test_lock_acquire_and_release(monkeypatch) -> None:
    acquired = {"true": True}
    calls: list[str] = []

    def fake_post(url, *a, **k):
        calls.append(url)
        if url.endswith("kv_acquire_lock"):
            return _Resp(200, "true" if acquired["true"] else "false")
        return _Resp(200, "")

    monkeypatch.setattr("state.supabase_state.requests.post", fake_post)

    backend = _backend()
    lock = backend.lock("refresh:x", 30)
    assert lock.acquire(blocking=False) is True
    acquired["true"] = False
    assert lock.acquire(blocking=False) is False
    lock.release()  # must not raise
    assert any(u.endswith("kv_release_lock") for u in calls)


def test_cache_get_returns_value_when_fresh(monkeypatch) -> None:
    expires = _iso(time.time() + 600)
    body = json.dumps([{"value": {"n": 1}, "expires_at": expires}])
    monkeypatch.setattr("state.supabase_state.requests.request", lambda *a, **k: _Resp(200, body))
    assert _backend().cache_get("llm", "abc") == {"n": 1}


def test_cache_get_treats_expired_as_miss(monkeypatch) -> None:
    body = json.dumps([{"value": "stale", "expires_at": _iso(time.time() - 1)}])
    monkeypatch.setattr("state.supabase_state.requests.request", lambda *a, **k: _Resp(200, body))
    assert _backend().cache_get("llm", "abc") is None


def test_cache_get_fails_open_on_error(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("state.supabase_state.requests.request", boom)
    assert _backend().cache_get("llm", "abc") is None


def test_cache_set_upserts_with_merge_duplicates(monkeypatch) -> None:
    seen: dict = {}

    def fake_request(method, url, json=None, headers=None, timeout=None):
        seen.update(method=method, url=url, json=json, headers=headers)
        return _Resp(201, "")

    monkeypatch.setattr("state.supabase_state.requests.request", fake_request)
    _backend().cache_set("tz", "site|me", "Asia/Kathmandu", 0)

    assert seen["method"] == "POST"
    assert "on_conflict=bucket,key" in seen["url"]
    assert seen["headers"]["Prefer"] == "resolution=merge-duplicates"
    assert seen["json"]["expires_at"] is None
    assert seen["json"]["value"] == "Asia/Kathmandu"


def test_disabled_backend_fails_open() -> None:
    backend = _backend()
    backend.base_url = ""
    backend.service_role_key = ""
    assert backend.enabled is False
    assert backend.check_rate("k", 1, 60) is True
    assert backend.acquire_lock("x", 30) is False
    assert backend.cache_get("llm", "k") is None
    backend.cache_set("llm", "k", "v", 60)  # no-op, must not raise
