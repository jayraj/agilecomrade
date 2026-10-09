import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jira_fetcher
from jira_fetcher import JiraFetcher


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_get_sprint_issues_follows_next_page_token(monkeypatch) -> None:
    pages = [
        {"issues": [{"key": "A"}], "nextPageToken": "t1", "isLast": False},
        {"issues": [{"key": "B"}], "nextPageToken": "t2", "isLast": False},
        {"issues": [{"key": "C"}], "isLast": True},
    ]
    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("params", {}))
        return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr(jira_fetcher, "_get", fake_get)

    f = object.__new__(JiraFetcher)
    f.base_url = "https://x.atlassian.net"
    f.auth = ("e", "t")
    f.headers = {"Accept": "application/json"}

    issues = f.get_sprint_issues(42)

    assert [i["key"] for i in issues] == ["A", "B", "C"]
    assert len(calls) == 3
    assert "nextPageToken" not in calls[0]
    assert calls[1]["nextPageToken"] == "t1"
    assert calls[2]["nextPageToken"] == "t2"


def test_get_sprint_issues_stops_on_error(monkeypatch):
    def boom(url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jira_fetcher, "_get", boom)

    f = object.__new__(JiraFetcher)
    f.base_url = "https://x.atlassian.net"
    f.auth = ("e", "t")
    f.headers = {"Accept": "application/json"}

    assert f.get_sprint_issues(42) == []