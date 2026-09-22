"""Builds the single /api/snapshot payload from raw fetched data + risks.

Consolidates the former /api/risk-radar, /api/blockers, /api/sprint-overview,
/api/next-sprint-overview, /api/velocity, /api/delivery-health, /api/risks,
/api/mitigations responses into one object so a serverless function can serve
the whole dashboard from a single cached snapshot.
"""

from datetime import datetime, timedelta

from risk_components import is_done, to_utc
from risk_engine import RiskEngine
from risk_explainer import (
    PENDING_STATUS,
    explain_risk,
    reconcile_decisions,
    stable_risk_id,
)

SPRINT_LEVEL_RISK_TYPES = ["BURNDOWN_BEHIND", "QA_BOTTLENECK", "BUG_RAISED", "SCOPE_CREEP", "SPRINT_ENDED_INCOMPLETE", "SPRINT_NOT_STARTED"]


def _issue_to_sprint_lookup(sprint_data):
    lookup = {}
    for data in sprint_data.values():
        sprint = data.get("sprint")
        if not sprint:
            continue
        for issue in data.get("issues", []):
            lookup[issue.get("key")] = sprint.get("name")
    return lookup


def _build_radar_data(sprint_data, risks):
    sprint_issues = {}
    for project_key, data in sprint_data.items():
        sprint = data.get("sprint")
        if sprint:
            sprint_issues[sprint.get("name")] = {
                "project_key": project_key,
                "start_date": sprint.get("startDate"),
                "end_date": sprint.get("endDate"),
                "issues": data.get("issues", []),
            }

    risks_by_sprint = {}
    for risk in risks:
        if risk.get("type") not in SPRINT_LEVEL_RISK_TYPES:
            continue
        sprint_name = risk.get("sprint_key")
        if not sprint_name:
            continue
        risks_by_sprint.setdefault(sprint_name, []).append(risk)

    SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

    def build_details(issues):
        return [
            {
                "key": issue.get("key"),
                "summary": issue.get("summary"),
                "status": issue.get("status"),
                "story_points": issue.get("story_points", 0),
                "assignee": issue.get("assignee", "Unassigned"),
                "due_date": issue.get("due_date"),
            }
            for issue in issues
        ]

    def risk_sort_key(risk):
        return (SEVERITY_ORDER.get(risk.get("severity"), 0), risk.get("risk_score", 0))

    def build_card(sprint_name, sprint_info, sprint_risks):
        details = build_details(sprint_info.get("issues", []))
        total_sp = sum(issue.get("story_points", 0) for issue in sprint_info.get("issues", []))
        completed_sp = sum(
            issue.get("story_points", 0)
            for issue in sprint_info.get("issues", [])
            if is_done(issue.get("status"))
        )

        if not sprint_risks:
            return {
                "issue_key": sprint_name,
                "sprint_key": sprint_name,
                "project_key": sprint_info.get("project_key", "N/A"),
                "start_date": sprint_info.get("start_date"),
                "end_date": sprint_info.get("end_date"),
                "risk_type": "ON_TRACK",
                "risk_types": [],
                "risk_score": 0,
                "raw_score": 0,
                "severity": "LOW",
                "summary": sprint_name,
                "assignee": "N/A",
                "issue_count": len(details),
                "total_sp": total_sp,
                "completed_sp": completed_sp,
                "burndown_gap_percent": None,
                "details": details,
            }

        worst = max(sprint_risks, key=risk_sort_key)
        ordered = sorted(sprint_risks, key=risk_sort_key, reverse=True)
        risk_types = []
        for risk in ordered:
            rtype = risk.get("type")
            if rtype and rtype not in risk_types:
                risk_types.append(rtype)

        return {
            "issue_key": worst.get("issue_key", sprint_name),
            "sprint_key": sprint_name,
            "project_key": sprint_info.get("project_key", "N/A"),
            "start_date": sprint_info.get("start_date"),
            "end_date": sprint_info.get("end_date"),
            "risk_type": worst.get("type"),
            "risk_types": risk_types,
            "risk_score": worst.get("risk_score"),
            "raw_score": worst.get("raw_score"),
            "severity": worst.get("severity"),
            "summary": worst.get("summary", sprint_name),
            "assignee": "N/A",
            "issue_count": len(details),
            "total_sp": total_sp,
            "completed_sp": completed_sp,
            "burndown_gap_percent": worst.get("burndown_gap_percent"),
            "details": details,
        }

    radar_data = []
    for sprint_name, sprint_info in sprint_issues.items():
        sprint_risks = risks_by_sprint.get(sprint_name, [])
        radar_data.append(build_card(sprint_name, sprint_info, sprint_risks))

    return radar_data


def _build_sprint_overview(sprint_data):
    overview = {"projects": [], "total_sp": 0, "completed_sp": 0, "in_progress_sp": 0, "total_issue_count": 0}

    for project_key, data in sprint_data.items():
        sprint = data.get("sprint")
        issues = data.get("issues", [])
        if not sprint:
            continue

        total_sp = sum(issue.get("story_points", 0) for issue in issues)
        completed_sp = sum(
            issue.get("story_points", 0)
            for issue in issues
            if is_done(issue.get("status"))
        )
        in_progress_sp = sum(
            issue.get("story_points", 0)
            for issue in issues
            if issue.get("status") in ["In Progress", "In Review", "In QA Review"]
        )

        overview["projects"].append({
            "project_key": project_key,
            "sprint_name": sprint.get("name"),
            "start_date": sprint.get("startDate"),
            "end_date": sprint.get("endDate"),
            "total_sp": total_sp,
            "completed_sp": completed_sp,
            "in_progress_sp": in_progress_sp,
            "remaining_sp": total_sp - completed_sp,
            "completion_percent": int((completed_sp / total_sp * 100) if total_sp > 0 else 0),
            "issue_count": len(issues),
        })

        overview["total_sp"] += total_sp
        overview["completed_sp"] += completed_sp
        overview["in_progress_sp"] += in_progress_sp
        overview["total_issue_count"] += len(issues)

    return overview


def _build_next_sprint_overview(next_sprint_data, risk_engine):
    overview = {"projects": [], "total_sp": 0, "issue_count": 0}

    for project_key, data in next_sprint_data.items():
        sprint = data.get("sprint")
        issues = data.get("issues", [])
        if not sprint:
            continue

        total_sp = sum(issue.get("story_points", 0) for issue in issues)
        issue_types = {}
        for issue in issues:
            itype = issue.get("issue_type") or "Other"
            issue_types[itype] = issue_types.get(itype, 0) + 1

        rule_risks = risk_engine.calculate_next_sprint_risks(issues)
        risk_score = risk_engine.aggregate_risk_score(rule_risks)

        at_risk_keys = set()
        for r in rule_risks:
            at_risk_keys.update(r.get("issue_keys") or [])

        overview["projects"].append({
            "project_key": project_key,
            "sprint_key": sprint.get("name"),
            "start_date": sprint.get("startDate"),
            "end_date": sprint.get("endDate"),
            "total_sp": total_sp,
            "issue_count": len(issues),
            "issue_types": issue_types,
            "risk_score": risk_score,
            "at_risk_count": len(at_risk_keys),
        })

        overview["total_sp"] += total_sp
        overview["issue_count"] += len(issues)

    return overview


def _build_delivery_health(sprint_data, next_sprint_data, velocity_data, risks, risk_engine):
    def _avg_velocity(project_key):
        sprints = velocity_data.get(project_key, [])
        if not sprints:
            return None
        completed = [s.get("completed_sp", 0) for s in sprints]
        return sum(completed) / len(completed)

    def _sprint_duration_days(sprint):
        try:
            start = datetime.fromisoformat(sprint["startDate"].replace("Z", "+00:00")).replace(tzinfo=None)
            end = datetime.fromisoformat(sprint["endDate"].replace("Z", "+00:00")).replace(tzinfo=None)
            return max((end - start).days, 1)
        except Exception:
            return 14

    projects = []
    for project_key in set(sprint_data.keys()) | set(next_sprint_data.keys()):
        data = sprint_data.get(project_key)
        if not data or not data.get("sprint"):
            data = next_sprint_data.get(project_key)
        if not data or not data.get("sprint"):
            continue

        sprint = data.get("sprint")
        is_upcoming = project_key in next_sprint_data and project_key not in sprint_data

        issues = data.get("issues", [])
        total_sp = sum(i.get("story_points", 0) for i in issues)
        completed_sp = sum(i.get("story_points", 0) for i in issues if is_done(i.get("status")))
        completion_percent = int((completed_sp / total_sp * 100) if total_sp > 0 else 0)

        sprint_name = sprint.get("name")
        sprint_risks = [r for r in risks if r.get("sprint_key") == sprint_name]

        burndown_gap = max(
            (r.get("burndown_gap_percent", 0) for r in sprint_risks if r.get("type") == "BURNDOWN_BEHIND"),
            default=0,
        )
        high_risks = [r for r in sprint_risks if r.get("severity") in ("CRITICAL", "HIGH")]
        medium_risks = [r for r in sprint_risks if r.get("severity") == "MEDIUM"]
        other_risks = [r for r in sprint_risks if r.get("type") not in ("BURNDOWN_BEHIND",)]

        why = None
        if high_risks or burndown_gap >= 25:
            rag = "RED"
            if high_risks:
                why = f"{len(high_risks)} high-severity risk(s): {', '.join(r.get('type') for r in high_risks[:2])}"
            else:
                why = f"Burndown {burndown_gap:.0f}% behind schedule"
        elif medium_risks or other_risks or burndown_gap >= 10:
            rag = "AMBER"
            reasons = []
            if burndown_gap >= 10:
                reasons.append(f"burndown {burndown_gap:.0f}% behind")
            if medium_risks:
                reasons.append(f"{len(medium_risks)} medium risk(s)")
            if other_risks:
                reasons.append(f"{len(other_risks)} other risk(s)")
            why = "; ".join(reasons)
        elif is_upcoming:
            pre_risks = risk_engine.calculate_next_sprint_risks(issues)
            if pre_risks:
                rag = "AMBER"
                why = f"Pre-planning: {len(pre_risks)} risk(s) (e.g. {pre_risks[0].get('type')})"
            else:
                rag = "GREEN"
        else:
            rag = "GREEN"

        planned_end = sprint.get("endDate")
        duration_days = _sprint_duration_days(sprint)
        avg_vel = _avg_velocity(project_key)

        forecast_date = None
        forecast_delay_days = 0
        if avg_vel and avg_vel > 0:
            remaining_sp = total_sp - completed_sp
            daily_rate = avg_vel / duration_days
            if daily_rate > 0 and remaining_sp > 0:
                forecast_days = remaining_sp / daily_rate
                forecast_date = (datetime.utcnow() + timedelta(days=forecast_days)).date().isoformat()
            elif remaining_sp <= 0:
                forecast_date = datetime.utcnow().date().isoformat()

        try:
            planned_end_dt = to_utc(planned_end) if planned_end else None
            if planned_end_dt and forecast_date:
                forecast_dt = datetime.strptime(forecast_date, "%Y-%m-%d")
                forecast_delay_days = (forecast_dt - planned_end_dt.replace(tzinfo=None, hour=0, minute=0, second=0)).days
        except Exception:
            forecast_delay_days = 0

        if forecast_date is None:
            timeline_risk_score = 60
        elif forecast_delay_days > 0:
            timeline_risk_score = min(100, 55 + forecast_delay_days * 15)
        else:
            timeline_risk_score = 20

        if rag == "AMBER":
            timeline_risk_score = max(timeline_risk_score, 45)
        elif rag == "RED":
            timeline_risk_score = max(timeline_risk_score, 75)

        timeline_risk_score = min(100, int(timeline_risk_score + burndown_gap * 0.5))

        projects.append({
            "project_key": project_key,
            "sprint_key": sprint_name,
            "status": "upcoming" if is_upcoming else "active",
            "start_date": sprint.get("startDate"),
            "planned_end_date": planned_end,
            "total_sp": total_sp,
            "completed_sp": completed_sp,
            "completion_percent": completion_percent,
            "burndown_gap_percent": burndown_gap,
            "avg_velocity": round(avg_vel, 1) if avg_vel else None,
            "rag": rag,
            "why": why,
            "forecast_date": forecast_date,
            "forecast_delay_days": forecast_delay_days,
            "timeline_risk_score": timeline_risk_score,
        })

    return projects


def build_snapshot(sprint_data, next_sprint_data, velocity_data, risks, burndown_history, mitigations, last_sync, scope_meta=None, jira_timezone=None, risk_decisions=None):
    risk_engine = RiskEngine()
    lookup = _issue_to_sprint_lookup(sprint_data)

    # Transparency enrichment: every risk gets a stable identity plus the
    # Signal / Suspected cause / Suggested action fields, and is linked to any
    # human decision the user recorded for it (persisted across syncs under the
    # snapshot's risk_decisions key, keyed by stable_risk_id). Risk ids are
    # assigned FIRST so reconcile_decisions can tell still-detected risks from
    # resolved ones (last_seen vs outcome: cleared).
    for risk in risks:
        if not risk.get("sprint_key"):
            risk["sprint_key"] = lookup.get(risk.get("issue_key"))
        risk["risk_id"] = stable_risk_id(risk)

    risk_decisions = reconcile_decisions(risks, risk_decisions or {})
    for risk in risks:
        explain_risk(risk)
        decision = risk_decisions.get(risk["risk_id"])
        risk["decision"] = decision or {"status": PENDING_STATUS}

    blockers = list(risks)
    for blocker in blockers:
        sprint_key = blocker.get("sprint_key")
        if not sprint_key:
            sprint_key = lookup.get(blocker.get("issue_key"))
        blocker["sprint_key"] = sprint_key

    return {
        "radar_data": _build_radar_data(sprint_data, risks),
        "blockers": blockers,
        "sprint_overview": _build_sprint_overview(sprint_data),
        "next_sprint_overview": _build_next_sprint_overview(next_sprint_data, risk_engine),
        "velocity": velocity_data,
        "delivery_health": _build_delivery_health(sprint_data, next_sprint_data, velocity_data, risks, risk_engine),
        "risks": risks,
        "total_risks": len(risks),
        "summary": risk_engine.generate_risk_summary(risks),
        "sprint_data": sprint_data,
        "next_sprint_data": next_sprint_data,
        "mitigations": mitigations,
        "burndown_history": burndown_history,
        "scope_meta": scope_meta or {"baselines": {}, "history": {}},
        "risk_decisions": risk_decisions,
        "last_sync": last_sync,
        "jira_timezone": jira_timezone,
    }