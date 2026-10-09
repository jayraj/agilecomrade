import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from state.memory import MemoryStateBackend


def test_check_rate_allows_up_to_max_then_blocks() -> None:
    backend = MemoryStateBackend()
    assert backend.check_rate("k", 2, 60) is True
    assert backend.check_rate("k", 2, 60) is True
    assert backend.check_rate("k", 2, 60) is False


def test_check_rate_resets_after_window() -> None:
    backend = MemoryStateBackend()
    assert backend.check_rate("k", 1, 0.01) is True
    assert backend.check_rate("k", 1, 0.01) is False
    time.sleep(0.02)
    assert backend.check_rate("k", 1, 0.01) is True


def test_cache_roundtrip_and_expiry() -> None:
    backend = MemoryStateBackend()
    backend.cache_set("llm", "a", {"x": 1}, 60)
    assert backend.cache_get("llm", "a") == {"x": 1}

    backend.cache_set("llm", "b", "value", 0.01)
    assert backend.cache_get("llm", "b") == "value"
    time.sleep(0.02)
    assert backend.cache_get("llm", "b") is None


def test_cache_ttl_zero_never_expires() -> None:
    backend = MemoryStateBackend()
    backend.cache_set("tz", "site", "Asia/Kathmandu", 0)
    time.sleep(0.02)
    assert backend.cache_get("tz", "site") == "Asia/Kathmandu"


def test_cache_namespaces_are_isolated() -> None:
    backend = MemoryStateBackend()
    backend.cache_set("llm", "k", "from-llm", 60)
    backend.cache_set("tz", "k", "from-tz", 0)
    assert backend.cache_get("llm", "k") == "from-llm"
    assert backend.cache_get("tz", "k") == "from-tz"


def test_cache_miss_returns_none() -> None:
    assert MemoryStateBackend().cache_get("llm", "absent") is None


def test_lock_same_name_is_shared_and_mutually_exclusive() -> None:
    backend = MemoryStateBackend()
    lock = backend.lock("refresh:acme", 30)
    assert backend.lock("refresh:acme", 30) is lock

    assert lock.acquire(blocking=False) is True
    assert lock.acquire(blocking=False) is False  # already held
    lock.release()
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_locks_are_independent_per_name() -> None:
    backend = MemoryStateBackend()
    first = backend.lock("refresh:a", 60)
    second = backend.lock("refresh:b", 60)
    assert first is not second

    first.acquire()
    assert first.acquire(blocking=False) is False  # a's lock is held
    assert second.acquire(blocking=False) is True  # b is unaffected
    first.release()
    second.release()


def test_locks_are_not_shared_across_backends() -> None:
    a = MemoryStateBackend().lock("refresh:x", 60)
    b = MemoryStateBackend().lock("refresh:x", 60)
    assert a is not b
