"""Shared v2 rubric scoring components.

Every risk-type rule calls into the same three tables (time pressure, workflow
stage, size weight) plus the per-sprint context helpers, so the model stays
consistent and tunable from config.
"""
from datetime import datetime, timezone

from config import settings

# Hours past which a ticket is considered stale / not progressing.
STALE_HOURS = settings.story_update_threshold_hours

# QA column naming varies across boards ("QA Review" vs "In QA Review").
QA_STATUS_NAMES = ("qa review", "in qa review")


def is_qa_status(status):
    """True when the status is a QA column regardless of board naming."""
    return (status or "").strip().lower() in QA_STATUS_NAMES


def is_done(status):
    """True when an issue's status means completed work."""
    return (status or "").strip().lower() == "done"


def to_utc(value):
    """Parse an ISO timestamp to an aware UTC datetime (handles Z and offsets)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc():
    return datetime.now(timezone.utc)


def pct_sprint_elapsed(sprint, now=None):
    """Fraction of the sprint elapsed (calendar days). Returns 0.0-1.0+."""
    if not sprint or not sprint.get("startDate") or not sprint.get("endDate"):
        return 1.0
    start = to_utc(sprint.get("startDate"))
    end = to_utc(sprint.get("endDate"))
    now = now or now_utc()
    if not start or not end:
        return 1.0
    duration = (end - start).days
    elapsed = (now - start).days
    if duration <= 0:
        return 1.0
    return max(0.0, elapsed / duration)


def days_remaining(sprint, now=None):
    """Calendar days until the sprint ends (min 1).

    Uses `duration - elapsed` (rubric convention) rather than `(end - now).days`
    so a day-2-of-5 sprint reports 3 remaining days regardless of time-of-day.
    """
    if not sprint or not sprint.get("startDate") or not sprint.get("endDate"):
        return 1
    start = to_utc(sprint.get("startDate"))
    end = to_utc(sprint.get("endDate"))
    now = now or now_utc()
    if not start or not end:
        return 1
    duration = (end - start).days
    elapsed = (now - start).days
    if duration <= 0:
        return 1
    return max(duration - elapsed, 1)


def time_pressure_multiplier(sprint, now=None):
    """Risk of the same magnitude is worse the closer the sprint is to ending."""
    pct = pct_sprint_elapsed(sprint, now=now)
    for threshold, multiplier in settings.time_pressure_table:
        if pct <= threshold:
            return multiplier
    return settings.time_pressure_table[-1][1]


def workflow_stage_weight(status):
    """Effort already sunk into a ticket makes losing it more costly."""
    if not status:
        return settings.default_stage_weight
    if "blocked" in status.lower():
        return settings.blocked_stage_weight
    return settings.workflow_stage_weights.get(status, settings.default_stage_weight)


def avg_sprint_sp(issues):
    """Mean story points across tickets that have an estimate (>0)."""
    sps = [i.get("story_points", 0) or 0 for i in issues if (i.get("story_points") or 0) > 0]
    return (sum(sps) / len(sps)) if sps else 0.0


def size_weight(ticket_sp, avg_sp):
    """Keeps small tickets from being ignored, but lets large tickets dominate."""
    avg_sp = avg_sp or 1.0
    weight = 0.7 + (ticket_sp / avg_sp) * 0.3
    return min(settings.size_weight_max, max(settings.size_weight_min, weight))


def hours_since(updated, now=None):
    """Hours since an ISO 'updated' timestamp, or None."""
    updated_time = to_utc(updated)
    if updated_time is None:
        return None
    now = now or now_utc()
    return (now - updated_time).total_seconds() / 3600


def assignee_factor(issues, assignee):
    """1.4 if the assignee has no other active tickets in progress; else 1.0."""
    if not assignee or assignee == "Unassigned":
        return 1.0
    now = now_utc()
    for issue in issues:
        if issue.get("assignee") != assignee:
            continue
        if is_done(issue.get("status")):
            continue
        h = hours_since(issue.get("updated"), now=now)
        if h is not None and h <= STALE_HOURS:
            return 1.0  # other work moving normally
    return settings.assignee_no_active_factor


def is_blocking_map(issues):
    """issue_key -> set of issue keys that list it in their blocked_by field."""
    blocking = {}
    for issue in issues:
        key = issue.get("key")
        blocked_by = issue.get("blocked_by")
        if not key or not blocked_by:
            continue
        blockers = blocked_by
        if isinstance(blockers, str):
            blockers = [blockers]
        for b in blockers:
            if isinstance(b, dict):
                b = b.get("key") or b.get("value")
            if b and str(b).strip():
                blocking.setdefault(str(b).strip(), set()).add(key)
    return blocking


# A ticket counts as a dependency risk only when it carries a real dependency
# signal: a structured `blocked_by` link OR explicit blocking language in the
# description. A bare party noun ("external tools", "vendor API") is context,
# not a blocker, so it classifies a dependency but never creates one.
DEPENDENCY_PHRASES = (
    "blocked by", "blocked on", "blocked until",
    "waiting for", "wait for", "waiting on", "awaiting",
    "depends on", "dependent on",
    "rely on", "relies on", "relying on",
    "pending from", "pending on",
    "on hold", "stuck on",
)


def has_dependency_signal(issue):
    """True when the issue is genuinely blocked on another party.

    A `blocked_by` link is authoritative. Failing that, the description must
    contain explicit blocking language ("waiting for ...", "blocked by ...");
    a descriptive mention of a party ("export to external tools") is not one.
    """
    if issue.get("blocked_by"):
        return True
    description = (issue.get("description") or "").lower()
    return any(p in description for p in DEPENDENCY_PHRASES)


def bucket_severity(score):
    """Severity buckets: 0-19 LOW, 20-59 MEDIUM, 60-79 HIGH, 80+ CRITICAL."""
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 20:
        return "MEDIUM"
    return "LOW"


def cap_score(raw):
    """Display cap: round and clamp to 100 for UI/severity purposes."""
    return min(100, int(round(raw)))


def qa_throughput_per_day(velocity_data, project_key):
    """Historical avg tickets cleared/day by QA — rolling 3-sprint window.

    Falls back to the configured default when no history exists.
    """
    sprints = (velocity_data or {}).get(project_key, [])
    window = sprints[-settings.qa_throughput_window:]
    cleared = [s.get("qa_cleared_count", 0) for s in window]
    days = [s.get("duration_days", 14) for s in window]
    total_cleared = sum(cleared)
    total_days = sum(days)
    if total_days > 0 and total_cleared > 0:
        return total_cleared / total_days
    return settings.qa_throughput_default


def trend_factor(burndown_history):
    """1.3 if gap flat/widening, 1.0 shrinking slowly, 0.7 shrinking fast.

    Requires at least 2 prior check-ins. With insufficient history we assume
    the gap is flat/widening (1.3) rather than neutral — a fresh instance
    should flag a behind sprint at least as urgently as a mature one, so
    severity stays reproducible across environments regardless of how long
    each has been syncing.
    """
    history = burndown_history or []
    if len(history) < 2:
        return settings.trend_flat
    latest = history[-1]
    previous = history[-2]
    delta = latest - previous  # negative = shrinking
    if delta <= 0:
        if previous > 0 and delta <= -max(previous * 0.2, 2.0):
            return settings.trend_fast
        return settings.trend_slow
    return settings.trend_flat