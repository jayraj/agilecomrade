import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.snapshot_service as snapshot_service


def test_serves_cached_snapshot_while_refresh_running():
    lock = snapshot_service._refresh_lock("b4-lock-test")
    lock.acquire()
    try:
        result = snapshot_service._refresh_snapshot(
            {"slug": "b4-lock-test"},
            None,
            existing_snapshot={"cached": True},
        )
    finally:
        lock.release()
    assert result == {"cached": True}


def test_refresh_rereads_row_inside_lock(monkeypatch):
    fresh = {"slug": "b4-fresh-test", "marker": "fresh"}
    seen: dict = {}

    class _FakeStore:
        @staticmethod
        def get_profile(slug):
            return fresh

    monkeypatch.setattr(snapshot_service, "store", _FakeStore())
    monkeypatch.setattr(
        snapshot_service,
        "_refresh_snapshot_locked",
        lambda row, config: seen.update(row=row) or {"ok": True},
    )

    result = snapshot_service._refresh_snapshot({"slug": "b4-fresh-test", "marker": "stale"}, None)

    assert result == {"ok": True}
    assert seen["row"] is fresh
