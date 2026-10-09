"""Snapshot refresh orchestration: per-profile locking, Jira fetch, persistence."""

import logging
from datetime import timedelta

from config import UserConfig, settings
from crypto import decrypt_strict
from jira_fetcher import JiraFetcher, fetch_all
from risk_components import now_utc, to_utc
from risk_engine import RiskEngine
from services import store
from snapshot import build_snapshot
from state import get_backend

logger = logging.getLogger(__name__)

# A refresh that overruns this is assumed crashed; its lock auto-expires so
# later requests aren't blocked forever (only meaningful for the shared backend).
_REFRESH_LOCK_TTL = 300  # seconds


def _trim_issue(issue: dict) -> dict:
    """Strips bulky fields from stored issues (changelog; long free text)."""
    trimmed = dict(issue)
    trimmed.pop("changelog", None)
    if isinstance(trimmed.get("description"), str):
        trimmed["description"] = trimmed["description"][:500]
    if isinstance(trimmed.get("acceptance_criteria"), str):
        trimmed["acceptance_criteria"] = trimmed["acceptance_criteria"][:500]
    return trimmed


def _trim_sprint_data(data: dict) -> dict:
    out = {}
    for project_key, bundle in data.items():
        out[project_key] = {
            "sprint": bundle.get("sprint"),
            "issues": [_trim_issue(i) for i in bundle.get("issues", [])],
        }
    return out


def _refresh_lock(slug: str):
    return get_backend().lock(f"refresh:{slug}", _REFRESH_LOCK_TTL)


def _refresh_snapshot(row: dict, config: UserConfig, existing_snapshot=None):
    """Serialise refreshes per profile, then merge against the freshest row.

    Only one refresh runs per profile at a time, and the merge inputs (burndown
    history, scope baselines, risk decisions) are read from the row *after*
    acquiring the lock so a concurrent write can't be clobbered. While a refresh
    is already running, a caller holding a cached snapshot gets it back
    immediately (stale-while-revalidate) instead of blocking or firing a
    duplicate Jira fetch.
    """
    slug = row.get("slug", "")
    lock = _refresh_lock(slug)
    if not lock.acquire(blocking=False):
        if existing_snapshot is not None:
            logger.info(f"♻️ Refresh already running for '{slug}' — serving cached snapshot.")
            return existing_snapshot
        lock.acquire()
    try:
        fresh = store.get_profile(slug) or row
        return _refresh_snapshot_locked(fresh, config)
    finally:
        lock.release()


def _refresh_snapshot_locked(row: dict, config: UserConfig):
    """Fetch fresh Jira data, recompute risks, persist snapshot + history.

    If the Jira fetch comes back completely empty (e.g. transient outage),
    the previously persisted snapshot is kept instead of wiping it.
    """
    logger.info(f"🔄 Fetching Jira data for profile '{row['slug']}'...")
    fetcher = JiraFetcher(config)
    data = fetch_all(fetcher)
    sprint_data = _trim_sprint_data(data["sprint_data"])
    next_sprint_data = _trim_sprint_data(data["next_sprint_data"])
    velocity_data = data["velocity_data"]
    # The Jira timezone (from /myself) is the single source of truth for
    # calendar-day math, so our dates match what the user sees in Jira —
    # regardless of where this server runs.
    jira_timezone = data.get("jira_timezone")

    if not sprint_data and not next_sprint_data:
        stale = row.get("snapshot")
        if stale:
            logger.warning(f"⚠️ Empty Jira fetch for '{row['slug']}' — keeping previous snapshot.")
            return stale

    burndown_history = row.get("burndown_history") or {}
    # Scope-creep state lives inside the persisted snapshot (no dedicated
    # Supabase column): baselines captured at first active sync + SP trail.
    # Human risk decisions are carried the same way so they survive re-syncs.
    prev_snapshot = row.get("snapshot") or {}
    scope_meta = prev_snapshot.get("scope_meta") or {"baselines": {}, "history": {}}
    risk_decisions = prev_snapshot.get("risk_decisions") or {}

    risk_engine = RiskEngine()

    # Append today's burndown gaps (capped history) before risk calc so the
    # trend factor sees the latest check-in.
    for project_data in sprint_data.values():
        sprint = project_data.get("sprint")
        if not sprint:
            continue
        gap = risk_engine.get_burndown_gap(sprint, project_data.get("issues", []))
        if gap is None:
            continue
        name = sprint.get("name")
        history = burndown_history.setdefault(name, [])
        history.append(gap["burndown_gap_percent"])
        if len(history) > settings.burndown_history_size:
            del history[: len(history) - settings.burndown_history_size]

    # Capture/extend scope-creep state for each ACTIVE sprint: baseline on
    # first sight (the planning commitment), then append today's total SP.
    now_iso = now_utc().isoformat()
    now = now_utc()
    for project_data in sprint_data.values():
        sprint = project_data.get("sprint")
        if not sprint or not sprint.get("name"):
            continue
        name = sprint["name"]
        issues = project_data.get("issues", [])
        total_sp = sum(i.get("story_points", 0) or 0 for i in issues)
        baselines = scope_meta.setdefault("baselines", {})
        if name not in baselines:
            start = to_utc(sprint.get("startDate"))
            late = bool(
                start and (now - start).total_seconds() > settings.scope_baseline_grace_hours * 3600
            )
            baselines[name] = {
                "total_sp": total_sp,
                "issues": {
                    i.get("key"): (i.get("story_points", 0) or 0)
                    for i in issues
                    if i.get("key")
                },
                "captured_at": now_iso,
                "late_capture": late,
            }
            if late:
                logger.info(f"📅 Late scope baseline for '{name}' ({total_sp} SP) — lower confidence.")
            else:
                logger.info(f"📅 Scope baseline captured for '{name}' ({total_sp} SP).")
        trail = scope_meta.setdefault("history", {}).setdefault(name, [])
        trail.append(total_sp)
        if len(trail) > settings.scope_history_size:
            del trail[: len(trail) - settings.scope_history_size]
        base = baselines[name]
        adds_now = sum(1 for i in issues if i.get("key") and i["key"] not in (base.get("issues") or {}))
        logger.info(
            f"🔭 Scope[{name}] baseline={base.get('total_sp')}SP current={total_sp}SP "
            f"trail={trail} adds_vs_baseline={adds_now} manual={base.get('manual', False)}"
        )

    risks = risk_engine.calculate_all_risks(
        sprint_data,
        velocity_data=velocity_data,
        burndown_history=burndown_history,
        scope_meta=scope_meta,
        jira_timezone=jira_timezone,
    )

    snapshot = build_snapshot(
        sprint_data=sprint_data,
        next_sprint_data=next_sprint_data,
        velocity_data=velocity_data,
        risks=risks,
        burndown_history=burndown_history,
        mitigations=[],
        last_sync=now_utc().isoformat(),
        scope_meta=scope_meta,
        jira_timezone=jira_timezone,
        risk_decisions=risk_decisions,
    )

    store.update_profile(row["slug"], {
        "snapshot": snapshot,
        "burndown_history": burndown_history,
        "fetched_at": now_utc().isoformat(),
    })

    logger.info(f"✅ Profile '{row['slug']}' synced. {len(risks)} risks.")
    return snapshot


def _get_or_refresh_snapshot(row: dict, allow_stale: bool = False):
    config = UserConfig.from_row(row, decrypt_strict)
    fetched_at = row.get("fetched_at")
    snapshot = row.get("snapshot")

    if snapshot:
        # AI generation doesn't need freshly-fetched Jira data — the page the
        # user is acting on already reflects the current snapshot. Reusing it
        # avoids a full Jira refetch on every "Mitigate/Scan/draft" click.
        if allow_stale:
            return snapshot, config
        try:
            fetched_dt = to_utc(fetched_at)
        except Exception:
            fetched_dt = None
        if fetched_dt and now_utc() - fetched_dt < timedelta(minutes=settings.sync_interval_minutes):
            return snapshot, config

    return _refresh_snapshot(row, config, existing_snapshot=snapshot), config
