import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_store import SupabaseStore


class _Resp:
    def __init__(self, status_code: int = 200, text: str = "[]"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def _store() -> SupabaseStore:
    return SupabaseStore(url="https://example.supabase.co", service_role_key="service-role-key")


def _capture(monkeypatch, status_code: int = 200, text: str = "[]"):
    """Record the (method, url, kwargs) of the single request the store makes."""
    seen = {}

    def fake_request(method, url, **kwargs):
        seen["method"] = method
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Resp(status_code, text)

    monkeypatch.setattr("supabase_store.requests.request", fake_request)
    return seen


def test_delete_omits_content_type(monkeypatch) -> None:
    """A bodyless DELETE must not advertise a JSON payload (PostgREST PGRST123)."""
    seen = _capture(monkeypatch, text='[{"slug": "acme"}]')
    _store().delete_profile("acme")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/rest/v1/profiles?slug=eq.acme")
    assert "Content-Type" not in seen["kwargs"]["headers"]


def test_delete_returns_the_removed_rows(monkeypatch) -> None:
    _capture(monkeypatch, text='[{"slug": "acme"}]')
    assert _store().delete_profile("acme") == [{"slug": "acme"}]


def test_delete_on_missing_row_returns_empty(monkeypatch) -> None:
    """Zero echoed rows means nothing matched — callers turn this into a 404."""
    _capture(monkeypatch, text="[]")
    assert _store().delete_profile("gone") == []


def test_payload_calls_keep_content_type(monkeypatch) -> None:
    seen = _capture(monkeypatch, text='[{"slug": "acme"}]')
    _store().create_profile({"slug": "acme"})
    assert seen["method"] == "POST"
    assert seen["kwargs"]["headers"]["Content-Type"] == "application/json"
    assert seen["kwargs"]["json"] == {"slug": "acme"}


def test_http_error_raises(monkeypatch) -> None:
    _capture(monkeypatch, status_code=400, text="{}")
    try:
        _store().delete_profile("acme")
    except RuntimeError as exc:
        assert "HTTP 400" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for a non-2xx response")
