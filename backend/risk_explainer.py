"""Deterministic per-risk transparency explainer.

Enriches every risk emitted by the v2 engine with the four-part
"Signal → Suspected cause → Suggested action → Human decision" view rendered in
the sprint-details screen. It only reads diagnostics the detectors already
produce — no network, no LLM — so explanations are reproducible and testable.

Also provides the stable `risk_id` (the key under which human decisions are
persisted across syncs) and reconciliation of the decision ledger.
"""
import hashlib
from datetime import datetime
from typing import Any

from risk_matrix import IMPACT_LABELS, PROBABILITY_LABELS

# Statuses a scrum master can attach to a risk (human decision). "pending" is
# the engine-authored default meaning no human decision has been recorded yet.
DECISION_STATUSES = ("accepted", "monitoring", "mitigating", "escalated", "dismissed")
PENDING_STATUS = "pending"

_MAX_DECISIONS = 200


def stable_risk_id(risk: dict) -> str:
    """Deterministic, sync-stable identity for a risk.

    Keyed on risk type + sprint + the single anchored issue key so a human
    decision made today re-attaches after the next sync even as symptom values
    (hours since update, gap %, …) and the volatile `issue_keys` set change.
    Ticket-level risks anchor on their `issue_key`; sprint-wide risks anchor
    on type + sprint only (each sprint-level detector emits at most one risk
    per type per sprint), keeping the id stable as issues open/close.
    """
    keys = [str(risk["issue_key"])] if risk.get("issue_key") else []
    base = f'{risk.get("type") or ""}:{risk.get("sprint_key") or "":}'
    base += ",".join(sorted(keys))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def _fmt(v: Any) -> str:
    """Render a fact value; number formatting keeps one decimal for floats."""
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _facts(pairs: list[tuple[str, Any]]) -> list[dict]:
    out = []
    for label, value in pairs:
        if value in (None, "", [], {}, ()):
            continue
        out.append({"label": label, "value": _fmt(value)})
    return out


def _conf(risk: dict) -> str:
    return str(risk.get("confidence") or 0)


# Score-to-severity bands, mirroring frontend/src/utils/format.ts:severityFromScore.
_SEVERITY_BANDS = {"LOW": "(<20)", "MEDIUM": "(20-59)", "HIGH": "(60-79)", "CRITICAL": "(80+)"}


def _severity_reason(risk: dict, drivers: str) -> str:
    """Deterministic 'why is this risk critical/medium/low?' sentence."""
    sev = (risk.get("severity") or "").upper()
    band = _SEVERITY_BANDS.get(sev, "")
    probability = risk.get("probability")
    impact = risk.get("impact")
    if probability and impact:
        matrix_value = risk.get("matrix_value") or probability * impact
        return (
            f"Why {sev}? {drivers} → P{probability} {PROBABILITY_LABELS.get(probability, '')}"
            f" × I{impact} {IMPACT_LABELS.get(impact, '')} = {matrix_value} → {sev} {band}"
        ).rstrip()
    raw = risk.get("raw_score")
    score = risk.get("risk_score")
    raw_str = f"{raw:.0f}" if isinstance(raw, (int, float)) else str(raw or "?")
    score_str = f"{score:.0f}" if isinstance(score, (int, float)) else str(score or "?")
    return f"Why {sev}? {drivers} → raw {raw_str} → score {score_str} → {sev} {band}".rstrip()


def _chips(items: list[str], limit: int = 3) -> str:
    if not items:
        return ""
    shown = ", ".join(items[:limit])
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{shown}{more}"


def _issue_keys(risk: dict) -> list[str]:
    return [str(k) for k in (risk.get("issue_keys") or [])]


def _last_update(risk: dict) -> float:
    return float(risk.get("hours_since_update") or 0)


def explain_risk(risk: dict) -> None:
    """Mutate `risk` in place, adding signal / suspected_cause / suggested_action."""
    rtype = risk.get("type")
    fn = _EXPLAINERS.get(rtype, _default_explainer)
    fn(risk)
    attach_factors(risk)


# Multipliers reused in the score math — kept symbolic here and mirrored in the
# engine's serialized factors so the chip label can show the exact same number
# that went into raw_score. This is an additive, explainer-only helper: it reads
# the structured engine factors already latched onto each risk and does not touch
# the scoring rubric.
def attach_factors(risk: dict) -> None:
    """Add an additive structured `factors` block for score math transparency.

    Renders the exact numeric drivers the engine used to compute this risk's
    score (band = score-band chips; weight/fan-out etc. = per-driver chips), and
    never string-parses severity_reason. The rubric and severity math are fully
    untouched — the block is purely additive serialization, added once here where
    every risk already passes through in both snapshot payload paths.
    """
    sev = (risk.get("severity") or "").upper()
    band = _SEVERITY_BANDS.get(sev, "")
    probability = risk.get("probability")
    impact = risk.get("impact")

    band_chips = []
    factor_chips = []
    if probability and impact:
        matrix_value = risk.get("matrix_value") or probability * impact
        band_chips.append(f"P{probability} × I{impact} = {matrix_value} → {sev} {band}".rstrip())
        band_chips.append(
            f"{PROBABILITY_LABELS.get(probability, '')} probability"
            f" × {IMPACT_LABELS.get(impact, '')} impact".replace("  ", " ")
        )
    else:
        raw = risk.get("raw_score")
        score = risk.get("risk_score")
        if band and sev:
            raw_str = _fmt(raw) if raw is not None else "?"
            score_str = _fmt(score) if score is not None else "?"
            band_chips.append(f"{sev} {band} (raw {raw_str} → score {score_str})".rstrip())

        # Multiplier drivers the engine already latches onto each risk. Only drivers
        # with a non-neutral value are shown.
        spec = [
            ("stage_weight", "stage", "🧱"),
            ("assignee_factor", "assignee", "🙋"),
            ("size_weight", "size", "⚖️"),
            ("fan_out", "fan-out", "🔀"),
        ]
        for key, label, icon in spec:
            value = risk.get(key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if abs(value - 1.0) < 0.001:
                continue
            factor_chips.append({"icon": icon, "label": f"{label} ×{value:g}"})

    risk["factors"] = {
        "band": band_chips,
        "drivers": factor_chips,
    }


_FACTOR_ICONS = {
    "fan_out": "🔀",
    "fan_out_count": "🔀",
    "assignee_count": "👥",
}


def _default_explainer(risk: dict) -> None:
    c = _conf(risk)
    keys = _issue_keys(risk)
    risk["signal"] = {
        "label": risk.get("summary") or risk.get("type") or "Risk",
        "facts": _facts([
            ("Affected tickets", _chips(keys)),
            ("Confidence", f"{c}%"),
            ("Severity", risk.get("severity")),
        ]),
    }
    risk["suspected_cause"] = (
        f"The '{risk.get('type')}' rule flagged this based on the sprint data "
        f"above (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"the '{risk.get('type')}' rule flagged this from the sprint data (confidence {c}%)",
    )


def _story_not_progressing(risk: dict) -> None:
    c = _conf(risk)
    h = _last_update(risk)
    key = risk.get("issue_key")
    risk["signal"] = {
        "label": f"{key} hasn't been updated in {h:.0f}h",
        "facts": _facts([
            ("Status", risk.get("status")),
            ("Assignee", risk.get("assignee")),
            ("Hours since update", f"{h:.0f}"),
        ]),
    }
    risk["suspected_cause"] = (
        f"'{key}' has had no activity for {h:.0f}h during the sprint — it may be "
        f"blocked, deprioritized, or waiting on someone (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{h:.0f}h of silence × stage weight {risk.get('stage_weight')} × "
        f"assignee load {risk.get('assignee_factor')} × ticket size {risk.get('size_weight')}",
    )


def _sprint_not_started(risk: dict) -> None:
    c = _conf(risk)
    days = int(risk.get("days_elapsed") or 0)
    open_count = int(risk.get("open_count") or 0)
    risk["signal"] = {
        "label": f"Sprint '{risk.get('sprint_key')}' started {days}d ago but work hasn't begun",
        "facts": _facts([
            ("Days elapsed", days),
            ("Open tickets", open_count),
            ("Tickets in progress", 0),
        ]),
    }
    risk["suspected_cause"] = (
        f"The sprint has been active {days}d yet all {open_count} open tickets are "
        f"still in a start column — work simply hasn't ticketed off "
        f"(confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"sprint started {days}d ago yet all {open_count} open tickets are still in a start column",
    )


def _burndown_behind(risk: dict) -> None:
    c = _conf(risk)
    gap = float(risk.get("burndown_gap_percent") or 0)
    risk["signal"] = {
        "label": f"Burndown is {gap:.0f}% behind plan",
        "facts": _facts([
            ("Remaining SP", round(float(risk.get("remaining_sp") or 0), 1)),
            ("Days remaining", risk.get("days_remaining")),
            ("Gap vs plan", f"{gap:.0f}%"),
            ("Completed SP", risk.get("completed_sp")),
        ]),
    }
    risk["suspected_cause"] = (
        f"Completion is lagging the ideal burn line — {risk.get('remaining_sp')} "
        f"SP still need to close in {risk.get('days_remaining')} days at the "
        f"current pace (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{gap:.0f}% gap vs the ideal burn line with only {risk.get('days_remaining')} days left",
    )


def _qa_bottleneck(risk: dict) -> None:
    c = _conf(risk)
    n = int(risk.get("qa_stories_count") or 0)
    stuck = len(risk.get("stuck_stories") or [])
    days_left = risk.get("days_remaining")
    clear_days = risk.get("backlog_clear_days")
    risk["signal"] = {
        "label": f"{n} stories are queued in QA review",
        "facts": _facts([
            ("In QA review", n),
            ("Stuck >24h", stuck),
            ("Time to clear queue", clear_days),
            ("Days remaining", days_left),
            ("QA throughput/day", risk.get("qa_throughput_per_day")),
        ]),
    }
    risk["suspected_cause"] = (
        f"The QA queue ({n} stories{', ' + str(stuck) + ' stuck over 24h' if stuck else ''}) "
        f"will take ~{clear_days or '?'} days to clear against only {days_left} days "
        f"left — QA capacity, not the team's development rate, is the constraint "
        f"(confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{n} stories queued needing ~{clear_days or '?'} days to clear but only {days_left} days left",
    )


def _external_dependency(risk: dict) -> None:
    c = _conf(risk)
    key = risk.get("issue_key")
    fan_out = risk.get("fan_out")
    risk["signal"] = {
        "label": f"{key} is waiting on an external party",
        "facts": _facts([
            ("Dependency", risk.get("dependency_detail")),
            ("Key", key),
            ("Blast radius (blocks other tickets)", fan_out if fan_out and float(fan_out) > 1 else None),
        ]),
    }
    risk["suspected_cause"] = (
        f"'{key}' depends on something outside the team's control "
        f"({risk.get('dependency_detail')}) and can stall even if all internal "
        f"work is done (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"'{key}' depends on {risk.get('dependency_detail') or 'an external party'} "
        f"(base {risk.get('dependency_base')} × fan-out {risk.get('fan_out')})",
    )


def _due_date_passed(risk: dict) -> None:
    c = _conf(risk)
    overdue = risk.get("overdue_issues") or []
    count = int(risk.get("count") or len(overdue))
    items = [f"{o.get('key')} ({o.get('days_overdue')}d)" for o in overdue]
    risk["signal"] = {
        "label": f"{count} ticket(s) are past their due date",
        "facts": _facts([
            ("Overdue", _chips(items)),
            ("Count", count),
        ]),
    }
    risk["suspected_cause"] = (
        f"{count} ticket(s) missed their committed delivery date and are still "
        f"open — either underestimated or blocked late in the sprint "
        f"(confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    max_days = max((int(o.get("days_overdue") or 0) for o in overdue), default=0)
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{count} ticket(s) past their due date, the worst by {max_days} day(s)",
    )


def _bug_raised(risk: dict) -> None:
    c = _conf(risk)
    key = risk.get("issue_key")
    tier = risk.get("tier") or risk.get("priority") or "defect"
    age = float(risk.get("days_since_created") or 0)
    fixed = (risk.get("status") or "").strip().lower() == "done"
    risk["signal"] = {
        "label": f"{tier} defect '{key}' raised in this sprint" if key else f"{tier} defect raised in this sprint",
        "facts": _facts([
            ("Priority", risk.get("priority")),
            ("Age (days)", round(age, 1)),
            ("Status", risk.get("status")),
            ("Tier", risk.get("tier")),
        ]),
    }
    risk["suspected_cause"] = (
        f"'{key}' is a {tier} in-sprint defect {age:.0f} day(s) old"
        f"{' — it was fixed but stays visible for regression risk' if fixed else ' and is still open'} "
        f"(confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{tier} defect '{key or 'unknown'}' is {age:.0f} day(s) old (priority {risk.get('priority')})",
    )


def _scope_creep(risk: dict) -> None:
    c = _conf(risk)
    growth = float(risk.get("growth_percent") or 0)
    baseline = risk.get("baseline_sp")
    current = risk.get("current_sp")
    added = len(risk.get("added_issues") or [])
    hiked = len(risk.get("story_point_hikes") or [])
    risk["signal"] = {
        "label": f"Sprint scope grew {growth:.0f}% vs the planning baseline",
        "facts": _facts([
            ("Baseline SP", round(float(baseline or 0), 1)),
            ("Current SP", round(float(current or 0), 1)),
            ("Growth", f"{growth:.0f}%"),
            ("Issues added after start", added),
            ("Estimate hikes", hiked),
        ]),
    }
    risk["suspected_cause"] = (
        f"The sprint started at {baseline} SP and now holds {current} SP "
        f"(+{growth:.0f}%) — work was added after kickoff or estimates were "
        f"re-estimated up (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{growth:.0f}% scope growth over baseline (+{added} issues, {hiked} estimate hike(s))",
    )


def _sprint_ended(risk: dict) -> None:
    c = _conf(risk)
    days_over = int(risk.get("days_overdue") or 0)
    remaining = risk.get("remaining_sp")
    risk["signal"] = {
        "label": f"Sprint ended {days_over}d ago with work incomplete",
        "facts": _facts([
            ("Days overdue", days_over),
            ("Remaining SP", round(float(remaining or 0), 1)),
            ("Completed SP", risk.get("completed_sp")),
        ]),
    }
    risk["suspected_cause"] = (
        f"The sprint's end date passed {days_over}d ago but it is still open in "
        f"Jira with {remaining} SP unfinished — the deadline was not met "
        f"(confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{days_over} day(s) overdue with {remaining} SP still unfinished",
    )


def _next_sprint_type(risk: dict) -> None:
    c = _conf(risk)
    count = int(risk.get("count") or 0)
    keys = _issue_keys(risk)
    rtype = risk.get("type")
    blurb = {
        "UNASSIGNED": "have no assignee",
        "UNESTIMATED": "lack story-point estimates",
        "UNDEFINED_SCOPE": "lack defined acceptance criteria",
        "SIZING_RISK": "are oversized (>8 SP)",
    }.get(rtype, "carry pre-planning risk")
    risk["signal"] = {
        "label": f"{count} planned issue(s) {blurb}",
        "facts": _facts([
            ("Affected tickets", _chips(keys)),
            ("Count", count),
        ]),
    }
    risk["suspected_cause"] = (
        f"{count} upcoming work item(s) {blurb} — planning gaps like this tend to "
        f"surface as mid-sprint delays once the sprint starts (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{count} planned issue(s) {blurb}",
    )


def _overloaded(risk: dict) -> None:
    c = _conf(risk)
    keys = _issue_keys(risk)
    assignee = risk.get("assignee") or "One teammate"
    count = int(risk.get("count") or len(keys) or 0)
    ratio = risk.get("load_ratio")
    ratio_str = f" ({ratio}x the team average)" if ratio else ""
    risk["signal"] = {
        "label": f"{assignee} is carrying {count} planned item(s){ratio_str}",
        "facts": _facts([
            ("Affected tickets", _chips(keys)),
            ("Open items for assignee", count),
            ("Team average", risk.get("team_average")),
            ("Story points on this assignee", risk.get("total_sp")),
        ]),
    }
    risk["suspected_cause"] = (
        f"{assignee} holds {count} of the sprint's planned items{ratio_str}, so the "
        f"work is concentrated on one person instead of spread across the team. "
        f"Concentrated load like this tends to surface as missed carry-over once "
        f"the sprint is underway (confidence {c}%)."
    )
    risk["suggested_action"] = (
        risk.get("recommendation") or risk.get("summary") or ""
    )
    risk["severity_reason"] = _severity_reason(
        risk,
        f"{assignee} carries {count} planned item(s){ratio_str}",
    )


_EXPLAINERS = {
    "STORY_NOT_PROGRESSING": _story_not_progressing,
    "SPRINT_NOT_STARTED": _sprint_not_started,
    "BURNDOWN_BEHIND": _burndown_behind,
    "QA_BOTTLENECK": _qa_bottleneck,
    "EXTERNAL_DEPENDENCY": _external_dependency,
    "DUE_DATE_PASSED": _due_date_passed,
    "BUG_RAISED": _bug_raised,
    "SCOPE_CREEP": _scope_creep,
    "SPRINT_ENDED_INCOMPLETE": _sprint_ended,
    "UNASSIGNED": _next_sprint_type,
    "UNESTIMATED": _next_sprint_type,
    "UNDEFINED_SCOPE": _next_sprint_type,
    "SIZING_RISK": _next_sprint_type,
    "OVERLOADED": _overloaded,
}

ALL_EXPLAINED_TYPES = set(_EXPLAINERS)


def reconcile_decisions(risks: list[dict], risk_decisions: dict) -> dict:
    """Reconcile the persisted decision ledger against the current risk set.

    - Touches `last_seen` on decisions whose risk is still detected.
    - Marks a decided-but-now-absent risk as cleared (unless already dismissed
      or resolved), so the ledger records the outcome of a decision.
    - Caps the ledger size to keep the snapshot bounded.
    """
    current_ids = {r.get("risk_id") for r in risks if r.get("risk_id")}
    now = datetime.utcnow().isoformat()
    out: dict = {}
    for rid, dec in risk_decisions.items():
        d = dict(dec or {})
        if rid in current_ids:
            d["last_seen"] = now
        elif d.get("status") not in ("dismissed",) and not d.get("outcome"):
            d["outcome"] = "cleared"
            d["resolved_at"] = now
        out[rid] = d
    ordered = sorted(out.items(), key=lambda kv: kv[1].get("decided_at") or "", reverse=True)
    return dict(ordered[:_MAX_DECISIONS])