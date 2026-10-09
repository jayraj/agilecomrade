"""AI + next-sprint endpoints (operate on the profile snapshot)."""

import logging
import time

from fastapi import APIRouter, Request

from api.deps import _auth, _error
from config import UserConfig
from crypto import decrypt_strict
from mitigation_agent import MitigationAgent
from risk_components import now_utc
from risk_engine import RiskEngine
from risk_explainer import DECISION_STATUSES, explain_risk
from services import store
from services.snapshot_service import _get_or_refresh_snapshot

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/risk-decision")
def set_risk_decision(request: Request, body: dict = None):
    """Record the scrum master's human decision on a detected risk.

    Body: {"risk_id": str, "status": one of DECISION_STATUSES, "note": str?, "owner": str?}
    Merged into the snapshot's risk_decisions ledger (keyed by stable risk_id)
    and persisted so it survives the next sync/re-detection.
    """
    row, error = _auth(request)
    if error:
        return error

    body = body or {}
    risk_id = (body.get("risk_id") or "").strip()
    status = (body.get("status") or "").strip().lower()
    if not risk_id or status not in DECISION_STATUSES:
        return _error(f"risk_id and a valid status ({', '.join(DECISION_STATUSES)}) are required", 400)

    snapshot, _ = _get_or_refresh_snapshot(row, allow_stale=True)
    if risk_id not in {r.get("risk_id") for r in snapshot.get("risks", [])}:
        return _error("Risk not found in the current snapshot — it may have resolved or moved", 404)

    decisions = dict(snapshot.get("risk_decisions") or {})
    decisions[risk_id] = {
        "status": status,
        "note": (body.get("note") or "").strip(),
        "owner": (body.get("owner") or "").strip(),
        "decided_at": now_utc().isoformat(),
    }

    updated = {**snapshot, "risk_decisions": decisions}
    for risk in updated.get("risks", []):
        if risk.get("risk_id") == risk_id:
            risk["decision"] = decisions[risk_id]
    store.update_profile(row["slug"], {"snapshot": updated})
    logger.info(f"🧑‍⚖️ Risk decision recorded | risk={risk_id} status={status} profile={row['slug']}")

    return {"status": "ok", "risk_id": risk_id, "decision": decisions[risk_id]}


@router.post("/api/generate-mitigations")
def generate_mitigations(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    t0 = time.time()
    snapshot, config = _get_or_refresh_snapshot(row, allow_stale=True)
    t_snap = time.time() - t0
    sprint_data = snapshot.get("sprint_data", {})
    lookup = {}
    for data in sprint_data.values():
        sprint = data.get("sprint")
        if not sprint:
            continue
        for issue in data.get("issues", []):
            lookup[issue.get("key")] = sprint.get("name")

    sprint_key_filter = (body or {}).get("sprint_key")

    sprints = {}
    for project_key, data in sprint_data.items():
        sprint = data.get("sprint")
        if not sprint:
            continue
        sprint_name = sprint.get("name")
        if sprint_key_filter and sprint_name != sprint_key_filter:
            continue
        if sprint_name not in sprints:
            sprints[sprint_name] = {
                "sprint_key": sprint_name,
                "project_key": project_key,
                "risks": [],
                "issues": data.get("issues", []),
            }

    for risk in snapshot.get("risks", []):
        sprint_key = risk.get("sprint_key") or lookup.get(risk.get("issue_key"))
        if not sprint_key:
            sprint_key = risk.get("issue_key", "Unknown")
        if sprint_key in sprints:
            sprints[sprint_key]["risks"].append(risk)

    agent = MitigationAgent(config)
    t_llm0 = time.time()
    mitigations = agent.generate_sprint_mitigation_plan(list(sprints.values()))
    logger.info(
        f"⏱️ generate_mitigations | snapshot={t_snap:.2f}s llm={time.time() - t_llm0:.2f}s "
        f"total={time.time() - t0:.2f}s sprints={len(sprints)}"
    )
    store.update_profile(row["slug"], {"snapshot": {**snapshot, "mitigations": mitigations}})

    return {
        "status": "generated",
        "mitigations": mitigations,
        "total": len(mitigations),
        "ai_used": all(m.get("ai_used", False) for m in mitigations) if mitigations else False,
        "llm": agent.get_model_info(),
    }


@router.post("/api/next-sprint-risks")
def next_sprint_risks(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    project_key = (body or {}).get("project_key")
    if not project_key:
        return _error("project_key is required", 400)

    t0 = time.time()
    snapshot, config = _get_or_refresh_snapshot(row, allow_stale=True)
    t_snap = time.time() - t0
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return _error(f"No next sprint found for project {project_key}", 404)

    issues = project_data.get("issues", [])
    risk_engine = RiskEngine()
    rule_based_risks = risk_engine.calculate_next_sprint_risks(issues)

    agent = MitigationAgent(config)
    t_llm0 = time.time()
    risks, ai_used, prompt, raw_response, ai_error = agent.analyze_next_sprint_risks(
        project_key=project_key,
        sprint=project_data.get("sprint", {}),
        issues=issues,
        rule_based_risks=rule_based_risks,
    )
    for r in risks:
        explain_risk(r)
    logger.info(
        f"⏱️ next_sprint_risks | snapshot={t_snap:.2f}s llm={time.time() - t_llm0:.2f}s "
        f"total={time.time() - t0:.2f}s project={project_key}"
    )

    return {
        "status": "analyzed",
        "project_key": project_key,
        "sprint_key": project_data["sprint"].get("name"),
        "risks": risks,
        "total": len(risks),
        "ai_used": ai_used,
        "error": ai_error,
        "prompt": prompt,
        "raw_response": raw_response,
        "llm": agent.get_model_info(),
    }


@router.post("/api/next-sprint-issues")
def next_sprint_issues(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    project_key = (body or {}).get("project_key")
    if not project_key:
        return _error("project_key is required", 400)

    snapshot, config = _get_or_refresh_snapshot(row)
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return _error(f"No next sprint found for project {project_key}", 404)

    issues = []
    for issue in project_data.get("issues", []):
        issues.append({
            "key": issue.get("key"),
            "summary": issue.get("summary"),
            "status": issue.get("status"),
            "assignee": issue.get("assignee", "Unassigned"),
            "story_points": issue.get("story_points", 0),
            "issue_type": issue.get("issue_type"),
            "due_date": issue.get("due_date"),
        })

    return {
        "status": "ok",
        "project_key": project_key,
        "sprint_key": project_data["sprint"].get("name"),
        "issues": issues,
        "total": len(issues),
    }


@router.post("/api/generate-followup-message")
def generate_followup_message(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    issue_key = (body or {}).get("issue_key")
    if not issue_key:
        return _error("issue_key is required", 400)

    t0 = time.time()
    config = UserConfig.from_row(row, decrypt_strict)
    blocker_in = (body or {}).get("blocker")

    if blocker_in:
        # Fast path: the UI already has the risk object, so skip the full Jira
        # snapshot rebuild entirely (was the main cost of per-ticket drafts).
        blocker = dict(blocker_in)
        blocker.setdefault("issue_key", issue_key)
        agent = MitigationAgent(config)
        result = agent.generate_followup_message(blocker)
        result["issue_key"] = issue_key
        logger.info(f"⏱️ generate_followup_message | fast-path (no snapshot) llm={time.time() - t0:.2f}s issue={issue_key}")
        return result

    snapshot, _ = _get_or_refresh_snapshot(row)
    blocker = next(
        (r for r in snapshot.get("risks", []) if r.get("issue_key") == issue_key),
        {},
    )
    blocker.setdefault("issue_key", issue_key)

    agent = MitigationAgent(config)
    result = agent.generate_followup_message(blocker)
    result["issue_key"] = issue_key
    logger.info(f"⏱️ generate_followup_message | snapshot-rebuild llm={time.time() - t0:.2f}s issue={issue_key}")
    return result
