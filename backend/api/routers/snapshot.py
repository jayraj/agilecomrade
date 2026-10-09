"""Snapshot endpoints: the single dashboard payload, manual sync, scope baseline."""

import logging

from fastapi import APIRouter, Request

from api.deps import _auth, _error
from config import UserConfig
from crypto import decrypt_strict
from risk_components import now_utc
from services import store
from services.snapshot_service import _get_or_refresh_snapshot, _refresh_snapshot

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/snapshot")
def get_snapshot(request: Request):
    row, error = _auth(request)
    if error:
        return error

    snapshot, _ = _get_or_refresh_snapshot(row)
    return snapshot


@router.post("/api/sync-now")
def sync_now(request: Request):
    row, error = _auth(request)
    if error:
        return error

    config = UserConfig.from_row(row, decrypt_strict)
    snapshot = _refresh_snapshot(row, config)
    return {
        "status": "synced",
        "risks_found": len(snapshot.get("risks", [])),
        "last_sync": snapshot.get("last_sync"),
    }


@router.post("/api/profiles/{slug}/scope-baseline")
def set_scope_baseline(slug: str, request: Request, body: dict = None):
    """Declare the true planning commitment for an active sprint.

    Body: {"sprint_name": str, "total_sp": number}
    Overwrites the auto-captured baseline (marks it manual) so scope creep
    is measured against the declared value from the next sync onward. The
    per-issue map is refreshed from the current snapshot so future adds and
    estimate hikes keep being detected.
    """
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return _error("slug mismatch", 403)

    sprint_name = (body or {}).get("sprint_name")
    total_sp = (body or {}).get("total_sp")
    if not sprint_name or total_sp is None or not isinstance(total_sp, (int, float)) or total_sp < 0:
        return _error("sprint_name and non-negative total_sp are required", 400)

    snapshot = _get_or_refresh_snapshot(row)[0]
    issues_by_sprint = {}
    for data in (snapshot.get("sprint_data") or {}).values():
        sprint = data.get("sprint") or {}
        if sprint.get("name"):
            issues_by_sprint[sprint["name"]] = data.get("issues", [])
    if sprint_name not in issues_by_sprint:
        return _error(f"No active sprint named '{sprint_name}' in the current snapshot", 404)

    issue_map = {
        i.get("key"): (i.get("story_points", 0) or 0)
        for i in issues_by_sprint[sprint_name]
        if i.get("key")
    }
    prev_scope_meta = snapshot.get("scope_meta") or {"baselines": {}, "history": {}}
    baselines = prev_scope_meta.setdefault("baselines", {})
    previous = baselines.get(sprint_name)
    baselines[sprint_name] = {
        "total_sp": total_sp,
        "issues": issue_map,
        "captured_at": now_utc().isoformat(),
        "late_capture": False,
        "manual": True,
    }

    updated = {**snapshot, "scope_meta": prev_scope_meta}
    store.update_profile(slug, {"snapshot": updated})
    logger.info(
        f"📅 Manual scope baseline set for '{sprint_name}': {total_sp} SP "
        f"(was {previous.get('total_sp') if previous else 'none'})."
    )
    return {
        "status": "baseline-set",
        "sprint_name": sprint_name,
        "total_sp": total_sp,
        "previous_total_sp": previous.get("total_sp") if previous else None,
        "tracked_issues": len(issue_map),
        "manual": True,
    }
