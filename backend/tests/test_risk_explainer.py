import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk_explainer import (
    PENDING_STATUS,
    DECISION_STATUSES,
    explain_risk,
    reconcile_decisions,
    stable_risk_id,
)


def _risk(**overrides):
    risk = {
        "type": "STORY_NOT_PROGRESSING",
        "sprint_key": "Sprint 42",
        "issue_key": "AC-101",
        "summary": "Integrate payments",
        "assignee": "Alice",
        "status": "In Progress",
        "hours_since_update": 36.0,
        "risk_score": 62,
        "severity": "HIGH",
        "confidence": 85,
        "recommendation": "Check with Alice for blockers.",
    }
    risk.update(overrides)
    return risk


def test_risk_id_is_deterministic_and_stable() -> None:
    a = stable_risk_id(_risk())
    b = stable_risk_id(_risk())
    assert a == b
    # Symptom values changing must NOT change the identity (decision stays attached).
    c = stable_risk_id(_risk(hours_since_update=60.0, risk_score=80, severity="CRITICAL"))
    assert a == c


def test_risk_id_branches_by_type_and_target() -> None:
    ticket = stable_risk_id(_risk())
    other_ticket = stable_risk_id(_risk(issue_key="AC-202"))
    assert ticket != other_ticket
    sprint = stable_risk_id(_risk(type="BURNDOWN_BEHIND", sprint_key="Sprint 42", issue_keys=["AC-101", "AC-202"]))
    assert sprint not in (ticket, other_ticket)


def test_sprint_level_risk_id_ignores_volatile_issue_keys() -> None:
    # Sprint-wide risks have no single issue_key; their id must survive the
    # issue_keys set changing between syncs (issues opening/closing) so a
    # recorded human decision re-attaches after a rebuild.
    a = stable_risk_id(
        {"type": "BURNDOWN_BEHIND", "sprint_key": "Sprint 42", "issue_keys": ["AC-101", "AC-102"]}
    )
    b = stable_risk_id(
        {"type": "BURNDOWN_BEHIND", "sprint_key": "Sprint 42", "issue_keys": ["AC-101", "AC-103"]}
    )
    assert a == b
    # Different sprints of the same type must stay distinct (no collision).
    c = stable_risk_id(
        {"type": "BURNDOWN_BEHIND", "sprint_key": "Sprint 41", "issue_keys": ["AC-101", "AC-102"]}
    )
    assert a != c


def test_story_signal_facts_and_cause() -> None:
    r = _risk()
    explain_risk(r)
    assert r["signal"]["label"] == "AC-101 hasn't been updated in 36h"
    values = {f["label"]: f["value"] for f in r["signal"]["facts"]}
    assert values["Hours since update"] == "36"
    assert values["Status"] == "In Progress"
    assert values["Assignee"] == "Alice"
    assert "36h" in r["suspected_cause"]
    assert "85%" in r["suspected_cause"]
    assert r["suggested_action"] == "Check with Alice for blockers."


def test_story_severity_reason_matches_band_and_drivers() -> None:
    r = _risk(
        severity="HIGH",
        risk_score=62,
        raw_score=68.2,
        stage_weight=1.2,
        assignee_factor=1.4,
        size_weight=0.9,
    )
    explain_risk(r)
    reason = r["severity_reason"]
    assert reason.startswith("Why HIGH?")
    assert "36h of silence" in reason
    assert "stage weight 1.2" in reason
    assert "assignee load 1.4" in reason
    assert "ticket size 0.9" in reason
    assert "raw 68" in reason
    assert "score 62" in reason
    assert "HIGH (60-79)" in reason


def test_burndown_severity_reason() -> None:
    r = _risk(
        type="BURNDOWN_BEHIND",
        issue_key=None,
        sprint_key="Sprint 42",
        burndown_gap_percent=18.4,
        remaining_sp=12.0,
        days_remaining=3,
        completed_sp=8,
        total_sp=20,
        severity="HIGH",
        risk_score=71,
        raw_score=71.4,
    )
    explain_risk(r)
    assert r["severity_reason"].startswith("Why HIGH?")
    assert "18% gap" in r["severity_reason"]
    assert "3 days left" in r["severity_reason"]
    assert "(60-79)" in r["severity_reason"]


def test_burndown_signal() -> None:
    r = _risk(
        type="BURNDOWN_BEHIND",
        issue_key=None,
        sprint_key="Sprint 42",
        burndown_gap_percent=18.4,
        remaining_sp=12.0,
        days_remaining=3,
        completed_sp=8,
        total_sp=20,
    )
    explain_risk(r)
    assert r["signal"]["label"] == "Burndown is 18% behind plan"
    values = {f["label"]: f["value"] for f in r["signal"]["facts"]}
    assert values["Remaining SP"] == "12.0"
    assert values["Days remaining"] == "3"


def test_unknown_type_gets_universal_fallback() -> None:
    r = _risk(type="MYSTERY_RULE", issue_key="AC-101")
    r.pop("hours_since_update", None)
    explain_risk(r)
    assert r["signal"]["facts"]
    assert "MYSTERY_RULE" in r["suspected_cause"]
    assert r["suggested_action"] == "Check with Alice for blockers."
    assert r["severity_reason"].startswith("Why HIGH?")


def test_reconcile_marks_cleared_and_touches_last_seen() -> None:
    risk = _risk()
    risk["risk_id"] = stable_risk_id(risk)
    ledger = {
        risk["risk_id"]: {"status": "monitoring", "decided_at": "2026-01-01T00:00:00+00:00"},
        "gone-risk-123": {"status": "mitigating", "decided_at": "2026-01-02T00:00:00+00:00"},
        "gone-dismissed-456": {"status": "dismissed", "decided_at": "2026-01-03T00:00:00+00:00"},
    }
    out = reconcile_decisions([risk], ledger)
    assert out[risk["risk_id"]]["last_seen"]  # still detected → marked as seen
    assert out["gone-risk-123"]["outcome"] == "cleared"  # gone → cleared
    assert out["gone-risk-123"]["resolved_at"]
    assert "outcome" not in out["gone-dismissed-456"]  # dismissed stays dismissed


def test_reconcile_does_not_reflags_already_cleared() -> None:
    out = reconcile_decisions(
        [],
        {"x": {"status": "monitoring", "decided_at": "2026-01-01T00:00:00+00:00", "outcome": "cleared"}},
    )
    assert out["x"]["outcome"] == "cleared"
    assert out["x"]["decided_at"] == "2026-01-01T00:00:00+00:00"


def test_status_contract() -> None:
    assert PENDING_STATUS == "pending"
    assert "accepted" in DECISION_STATUSES
    assert "monitoring" in DECISION_STATUSES
    assert "mitigating" in DECISION_STATUSES
    assert "escalated" in DECISION_STATUSES
    assert "dismissed" in DECISION_STATUSES