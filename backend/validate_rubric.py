"""Validate the v2 rubric scoring against the rubric's worked examples.

Usage:  python validate_rubric.py
Each example is reconstructed as synthetic sprint/ticket data and the produced
scores are asserted with a small tolerance (scores are rounded ints).
"""
import json
import os
from datetime import datetime, timedelta

from config import settings as _settings
from risk_components import (
    STALE_HOURS,
    avg_sprint_sp,
    is_blocking_map,
    time_pressure_multiplier,
)
from risk_engine import RiskEngine
from risk_matrix import assert_projection_is_band_aligned

eng = RiskEngine()


def settings_scope_cap():
    return _settings.scope_creep_cap


def _iso(days_from_now):
    return (datetime.utcnow() + timedelta(days=days_from_now)).isoformat() + "Z"


def _sprint(days_elapsed, duration, name="Sprint X"):
    """Sprint starting `days_elapsed` days ago, ending `duration - days_elapsed` from now."""
    return {
        "name": name,
        "startDate": _iso(-days_elapsed),
        "endDate": _iso(duration - days_elapsed),
    }


def _issue(key, status, sp, updated_hours_ago, assignee="Alice", due=None, blocked_by=None,
           priority=None, issue_type=None, created_days_ago=None, labels=None):
    issue = {
        "key": key,
        "summary": f"{key} summary",
        "status": status,
        "story_points": sp,
        "assignee": assignee,
        "updated": _iso(-updated_hours_ago / 24),
        "description": "",
    }
    if due:
        issue["due_date"] = due
    if blocked_by:
        issue["blocked_by"] = blocked_by
    if priority:
        issue["priority"] = priority
    if issue_type:
        issue["issue_type"] = issue_type
    if created_days_ago is not None:
        issue["created"] = _iso(-created_days_ago)
    if labels:
        issue["labels"] = labels
    return issue


def check(name, actual, expected, tol=2):
    ok = abs(actual - expected) <= tol
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: expected {expected}, got {actual}")
    return ok


def _first(risks, label):
    if not risks:
        raise AssertionError(f"{label}: engine returned no risks (expected at least one)")
    return risks[0]


def run():
    results = []

    # Hard invariant (not a counted check): the 5x5 matrix projection must stay
    # band-aligned for all 25 products, so bucket_severity(project) always
    # equals the matrix band. If this ever drifts, severities disagree.
    assert_projection_is_band_aligned()

    # ------------------------------------------------------------------ #
    # BURNDOWN_BEHIND
    # ------------------------------------------------------------------ #
    print("\nBURNDOWN_BEHIND")
    # Example A: gap=100 (capped 60), day 4/4 (mult 1.7), flat trend (1.3) -> 60*1.3*1.7 = 132 -> 100 CRITICAL
    sprint_a = _sprint(days_elapsed=4, duration=4, name="PFIN Sprint 4")
    issues_a = [_issue("P-1", "To Do", 8, 10), _issue("P-2", "To Do", 8, 10)]
    history_a = [5, 100]  # widening -> flat
    risks_a = eng.detect_burndown_risks(sprint_a, issues_a, {"burndown_history": history_a})
    r = _first(risks_a, "BURNDOWN_BEHIND A")
    assert r["type"] == "BURNDOWN_BEHIND", r
    results.append(check(f"A: {sprint_a['name']} (gap 100, day4/4, flat)", r["risk_score"], 100))
    results.append(check("A severity", 1 if r["severity"] == "CRITICAL" else 0, 1, tol=0))

    # Example B: same issue set, day 1/4 (mult 0.6) -> strictly lower urgency
    sprint_b = _sprint(days_elapsed=1, duration=4, name="PFIN Sprint 4")
    risks_b = eng.detect_burndown_risks(sprint_b, issues_a, {"burndown_history": history_a})
    results.append(check("B: time_pressure(0.25) == 0.6", time_pressure_multiplier(sprint_b), 0.6, tol=0.01))
    results.append(check("B: day1/4 score < day4/4 score (time pressure works)",
                         1 if _first(risks_b, "BURNDOWN_BEHIND B")["risk_score"] < risks_a[0]["risk_score"] else 0, 1, tol=0))

    # ------------------------------------------------------------------ #
    # DUE_DATE_PASSED
    # ------------------------------------------------------------------ #
    print("\nDUE_DATE_PASSED")
    # Example: PFIN-10 1 day overdue, Code Review (1.1), 5 SP (size 1.0), blocks 1 (1.3)
    # base=min(70,15)=15 -> 15*1.1*1.0*1.3 = 21.45 -> 21 LOW-MEDIUM
    sprint_d = _sprint(1, 5)
    issues_d = [
        _issue("PFIN-10", "Code Review", 5, 6, due=(datetime.utcnow() - timedelta(days=1)).date().isoformat()),
        _issue("PFIN-11", "To Do", 5, 6, blocked_by="PFIN-10"),
    ]
    ctx_d = {"avg_sp": avg_sprint_sp(issues_d), "blocking_map": is_blocking_map(issues_d)}
    risks_d = eng.detect_due_date_risks(sprint_d, issues_d, ctx_d)
    r = _first(risks_d, "DUE_DATE_PASSED example")
    results.append(check("Example: 1 day overdue, CR, 5SP, blocks 1", r["risk_score"], 21))

    # Counter-example: 4 days overdue, QA (1.3), blocks 2 (1.3)
    # base=min(70,60)=60 -> 60*1.3*1.0*1.3 = 101.4 -> 100 HIGH
    issues_d2 = [
        _issue("PFIN-10", "In QA Review", 5, 6, due=(datetime.utcnow() - timedelta(days=4)).date().isoformat()),
        _issue("PFIN-11", "To Do", 5, 6, blocked_by="PFIN-10"),
        _issue("PFIN-12", "To Do", 5, 6, blocked_by="PFIN-10"),
    ]
    ctx_d2 = {"avg_sp": avg_sprint_sp(issues_d2), "blocking_map": is_blocking_map(issues_d2)}
    risks_d2 = eng.detect_due_date_risks(sprint_d, issues_d2, ctx_d2)
    r = _first(risks_d2, "DUE_DATE_PASSED counter")
    results.append(check("Counter: 4 days overdue, QA, blocks 2", r["risk_score"], 100))

    # ------------------------------------------------------------------ #
    # BUG_RAISED (in-sprint defect)
    # ------------------------------------------------------------------ #
    print("\nBUG_RAISED")
    # Band model: tier derived from priority; score interpolates within the
    # tier band by age across the sprint length. sprint_b is 10 days long.
    sprint_b = _sprint(days_elapsed=4, duration=10)
    # P2 (High), 4/10 days old -> 30 + (50-30)*0.4 = 38 MEDIUM
    issues_b = [
        _issue("PFIN-50", "In QA Review", 2, 2, priority="High", issue_type="Bug",
               created_days_ago=4),
        _issue("PFIN-49", "In Progress", 3, 2, issue_type="Story", created_days_ago=5),
    ]
    risks_b = eng.detect_bug_risks(sprint_b, issues_b)
    r = _first(risks_b, "BUG_RAISED")
    results.append(check("P2 mid-age in band", r["risk_score"], 38))
    results.append(check("P2 severity MEDIUM", 1 if r["severity"] == "MEDIUM" else 0, 1, tol=0))

    # Counter: P4 (Low), fresh today -> band low 10 LOW
    issues_b2 = [
        _issue("PFIN-51", "To Do", 1, 1, priority="Low", issue_type="Bug",
               created_days_ago=0),
    ]
    risks_b2 = eng.detect_bug_risks(sprint_b, issues_b2)
    r = risks_b2[0]
    results.append(check("P4 fresh at band low", r["risk_score"], 10))

    # Filter: bug created BEFORE sprint start is not an in-sprint defect
    issues_b3 = [
        _issue("PFIN-52", "To Do", 2, 2, priority="High", issue_type="Bug",
               created_days_ago=6),
    ]
    risks_b3 = eng.detect_bug_risks(_sprint(days_elapsed=4, duration=10), issues_b3)
    results.append(check("Filter: pre-sprint bug not flagged", 0 if not risks_b3 else 1, 0))

    # P1 open (Highest, To Do, fresh) -> P1_open band low 80 CRITICAL
    issues_b4 = [
        _issue("PFIN-53", "To Do", 1, 1, priority="Highest", issue_type="Bug",
               created_days_ago=0),
    ]
    risks_b4 = eng.detect_bug_risks(sprint_b, issues_b4)
    r = risks_b4[0]
    results.append(check("P1 open at band low", r["risk_score"], 80))
    results.append(check("P1 open severity CRITICAL", 1 if r["severity"] == "CRITICAL" else 0, 1, tol=0))

    # P1 fixed before sprint end (Done) -> P1_fixed: 60 + (70-60)*0.4 = 64 HIGH
    issues_b5 = [
        _issue("PFIN-54", "Done", 2, 2, priority="Highest", issue_type="Bug",
               created_days_ago=4),
    ]
    risks_b5 = eng.detect_bug_risks(sprint_b, issues_b5)
    r = risks_b5[0]
    results.append(check("P1 fixed in band", r["risk_score"], 64))
    results.append(check("P1 fixed severity HIGH", 1 if r["severity"] == "HIGH" else 0, 1, tol=0))

    # Fixed lower-tier defects are normal quality variation -> skipped
    issues_b6 = [
        _issue("PFIN-55", "Done", 1, 1, priority="High", issue_type="Bug",
               created_days_ago=2),
    ]
    risks_b6 = eng.detect_bug_risks(sprint_b, issues_b6)
    results.append(check("Fixed P2 skipped", 0 if not risks_b6 else 1, 0))

    # Prod-escaped P1 (label) -> 100 CRITICAL regardless of status/age
    issues_b7 = [
        _issue("PFIN-56", "To Do", 1, 1, priority="Highest", issue_type="Bug",
               created_days_ago=1, labels=["production"]),
    ]
    risks_b7 = eng.detect_bug_risks(sprint_b, issues_b7)
    r = risks_b7[0]
    results.append(check("Prod-escaped P1 max score", r["risk_score"], 100))
    results.append(check("Prod-escaped severity CRITICAL", 1 if r["severity"] == "CRITICAL" else 0, 1, tol=0))

    # Unknown/missing priority defaults to P3
    issues_b8 = [
        _issue("PFIN-57", "To Do", 1, 1, issue_type="Bug", created_days_ago=0),
    ]
    risks_b8 = eng.detect_bug_risks(sprint_b, issues_b8)
    r = risks_b8[0]
    results.append(check("Unknown priority defaults to P3", 1 if r["tier"] == "P3" else 0, 1, tol=0))

    # ------------------------------------------------------------------ #
    # QA_BOTTLENECK
    # ------------------------------------------------------------------ #
    print("\nQA_BOTTLENECK")
    # Example: 2 in QA (both >24h stale), throughput 1/day -> clear 2d; 3 days left.
    # P: clear_ratio=2/3 -> Unlikely(2). I: queue 2 -> Minor(2), +1 stuck -> 3.
    # matrix=2x3=6 -> MEDIUM -> project(6)=30
    sprint_q = _sprint(days_elapsed=2, duration=5)
    issues_q = [
        _issue("Q-1", "In QA Review", 3, 30),
        _issue("Q-2", "In QA Review", 5, 30),
    ]
    ctx_q = {"qa_throughput": 1.0}
    risks_q = eng.detect_qa_bottleneck(sprint_q, issues_q, ctx_q)
    r = risks_q[0]
    results.append(check("Example: 2 QA, throughput 1/day, 3 days left", r["risk_score"], 30))
    results.append(check("Example severity MEDIUM", 1 if r["severity"] == "MEDIUM" else 0, 1, tol=0))

    # Counter: 2 QA, 1 day left. P: clear_ratio=2/1 -> Likely(4). I: 3.
    # matrix=4x3=12 -> HIGH -> project(12)=70
    sprint_q2 = _sprint(days_elapsed=4, duration=5)
    risks_q2 = eng.detect_qa_bottleneck(sprint_q2, issues_q, ctx_q)
    r = risks_q2[0]
    results.append(check("Counter: 2 QA, 1 day left", r["risk_score"], 70))

    # Board-naming robustness: "QA Review" (without "In") matches too
    issues_q3 = [
        _issue("Q-1", "QA Review", 3, 30),
        _issue("Q-2", "QA Review", 5, 30),
    ]
    risks_q3 = eng.detect_qa_bottleneck(sprint_q, issues_q3, ctx_q)
    results.append(check("'QA Review' variant detected", 1 if risks_q3 else 0, 1, tol=0))
    if risks_q3:
        results.append(check("'QA Review' variant same score", risks_q3[0]["risk_score"], 30))

    # ------------------------------------------------------------------ #
    # EXTERNAL_DEPENDENCY
    # ------------------------------------------------------------------ #
    print("\nEXTERNAL_DEPENDENCY")
    # Example: external "waiting for vendor credentials", blocks 2 (1.3), 5 SP (size 1.0)
    # 75*1.3*1.0 = 97.5 -> 97/98 HIGH
    sprint_e = _sprint(1, 5)
    issues_e = [
        _issue("E-1", "In Progress", 5, 6, blocked_by="DEP-1"),
        _issue("E-2", "To Do", 5, 6, blocked_by="DEP-1"),
        _issue("DEP-1", "In Progress", 5, 6, blocked_by="DEP-1")
        | {"description": "Blocked: waiting for vendor credentials from external vendor"},
    ]
    ctx_e = {"avg_sp": avg_sprint_sp(issues_e), "blocking_map": is_blocking_map(issues_e)}
    risks_e = eng.detect_external_dependencies(issues_e, ctx_e)
    dep = [r for r in risks_e if r["issue_key"] == "DEP-1"]
    r = dep[0]
    results.append(check("Example: external vendor, blocks 2, 5SP", r["risk_score"], 97, tol=2))
    results.append(check("Example severity CRITICAL", 1 if r["severity"] == "CRITICAL" else 0, 1, tol=0))

    # Counter: internal "depends on Platform team's API", nothing blocked, 2 SP
    # 50*1.0*size(2SP, avg5) = 50*0.82 = 41 MEDIUM
    issues_e2 = [
        _issue("E-3", "To Do", 8, 6),
        _issue("DEP-2", "In Progress", 2, 6)
        | {"description": "Depends on the Platform team's API"},
    ]
    ctx_e2 = {"avg_sp": avg_sprint_sp(issues_e2), "blocking_map": is_blocking_map(issues_e2)}
    risks_e2 = eng.detect_external_dependencies(issues_e2, ctx_e2)
    r = [x for x in risks_e2 if x["issue_key"] == "DEP-2"][0]
    results.append(check("Counter: internal, no fan-out, 2SP", r["risk_score"], 41, tol=3))

    # ------------------------------------------------------------------ #
    # SPRINT_ENDED_INCOMPLETE (overdue sprint still open in Jira)
    # ------------------------------------------------------------------ #
    print("\nSPRINT_ENDED_INCOMPLETE")
    # Ended 1 day ago with incomplete work: 16 SP total, 8 Done, 8 remaining.
    # P=5 (already ended); I: half the sprint unfinished -> Major(4).
    # matrix=5x4=20 -> CRITICAL -> project(20)=90
    sprint_o = _sprint(days_elapsed=5, duration=4, name="PFIN Sprint 6")
    issues_o = [_issue("P-1", "Done", 8, 10), _issue("P-2", "To Do", 8, 10)]
    risks_o = eng.detect_sprint_overdue_risk(sprint_o, issues_o)
    r = _first(risks_o, "SPRINT_ENDED_INCOMPLETE incomplete")
    results.append(check("Ended+incomplete: fires SPRINT_ENDED_INCOMPLETE",
                         1 if r["type"] == "SPRINT_ENDED_INCOMPLETE" else 0, 1, tol=0))
    results.append(check("Ended+incomplete: score 90 (CRITICAL)", r["risk_score"], 90))
    results.append(check("Ended+incomplete: remaining_sp 8", r["remaining_sp"], 8, tol=0))

    # Ended 1 day ago but all work Done in Jira -> still flagged (sprint not closed).
    # P=5; I: nothing left undone -> Insignificant(1). matrix=5x1=5 -> MEDIUM -> 20
    issues_o2 = [_issue("P-1", "Done", 8, 10), _issue("P-2", "Done", 8, 10)]
    risks_o2 = eng.detect_sprint_overdue_risk(sprint_o, issues_o2)
    r = _first(risks_o2, "SPRINT_ENDED_INCOMPLETE all-done")
    results.append(check("Ended+all-Done: still flagged (open past deadline)",
                         1 if r["type"] == "SPRINT_ENDED_INCOMPLETE" else 0, 1, tol=0))
    results.append(check("Ended+all-Done: score 20 (MEDIUM)", r["risk_score"], 20))

    # Sprint still in flight -> no overdue risk
    sprint_live = _sprint(days_elapsed=2, duration=4, name="PFIN Sprint 6")
    risks_live = eng.detect_sprint_overdue_risk(sprint_live, issues_o)
    results.append(check("In-flight sprint: no overdue risk", 0 if not risks_live else 1, 0))

    # Timezone-aware overdue math: regression lock for the Nepal (+5:45) boundary
    # where PFIN (23/Aug 18:15Z) and MOS (24/Aug 12:45Z) must both resolve to
    # 24/Aug in Jira and therefore the SAME days_overdue. In raw UTC they fall on
    # different calendar days (23 vs 24 Aug) and would wrongly differ by one.
    tz_pfin = {
        "name": "PFIN Sprint 6",
        "startDate": "2026-08-09T18:15:00.000Z",
        "endDate": "2026-08-23T18:15:00.000Z",
        "state": "active",
    }
    tz_mos = {
        "name": "MOS Sprint 5",
        "startDate": "2026-08-10T12:45:00.000Z",
        "endDate": "2026-08-24T12:45:00.000Z",
        "state": "active",
    }
    tz_issues = [_issue("P-1", "Done", 5, 10), _issue("P-2", "Done", 5, 10)]

    r_pfin_utc = _first(eng.detect_sprint_overdue_risk(tz_pfin, tz_issues), "tz pfin utc")
    r_mos_utc = _first(eng.detect_sprint_overdue_risk(tz_mos, tz_issues), "tz mos utc")
    results.append(check("UTC: PFIN vs MOS end dates differ by 1 day",
                         abs(r_pfin_utc["days_overdue"] - r_mos_utc["days_overdue"]), 1, tol=0))

    r_pfin_kat = _first(eng.detect_sprint_overdue_risk(tz_pfin, tz_issues, None, "Asia/Kathmandu"),
                        "tz pfin kathmandu")
    r_mos_kat = _first(eng.detect_sprint_overdue_risk(tz_mos, tz_issues, None, "Asia/Kathmandu"),
                       "tz mos kathmandu")
    results.append(check("Kathmandu: PFIN and MOS same days_overdue (both 24/Aug)",
                         r_pfin_kat["days_overdue"] - r_mos_kat["days_overdue"], 0, tol=0))
    results.append(check("Kathmandu shifts PFIN boundary (1 day less than UTC)",
                         r_pfin_utc["days_overdue"] - r_pfin_kat["days_overdue"], 1, tol=0))

    # ------------------------------------------------------------------ #
    # Isolation: one detector raising must not abort the others
    # ------------------------------------------------------------------ #
    print("\nDETECTOR ISOLATION")
    def _boom(self, *a, **k):
        raise RuntimeError("injected detector failure")
    eng.detect_scope_creep = _boom
    iso_sprint = _sprint(days_elapsed=5, duration=4, name="ISO Sprint")
    iso_issues = [_issue("P-1", "Done", 8, 10), _issue("P-2", "To Do", 8, 10)]
    iso_data = {"PROJ": {"sprint": iso_sprint, "issues": iso_issues}}
    iso_risks = eng.calculate_all_risks(iso_data)
    del eng.detect_scope_creep  # restore real method
    iso_types = {r["type"] for r in iso_risks}
    results.append(check("Isolation: overdue detector still runs when scope_creep raises",
                         1 if "SPRINT_ENDED_INCOMPLETE" in iso_types else 0, 1, tol=0))

    # ------------------------------------------------------------------ #
    # Privacy: LLM prompt sanitization (prompt_privacy)
    # ------------------------------------------------------------------ #
    from prompt_privacy import pseudonymize_assignee, scrub_emails

    mapping = {}
    assert pseudonymize_assignee("Sarah Chen", mapping) == "dev-01"
    assert pseudonymize_assignee("Ravi Patel", mapping) == "dev-02"
    results.append(check("Privacy: pseudonyms assigned in order, shared map", 1, 1, tol=0))
    results.append(check("Privacy: same person maps consistently",
                         1 if pseudonymize_assignee("Sarah Chen", mapping) == "dev-01" else 0, 1, tol=0))
    results.append(check("Privacy: Unassigned passes through",
                         1 if pseudonymize_assignee(None, mapping) == "Unassigned" else 0, 1, tol=0))

    scrubbed = scrub_emails("Contact bob@corp.com about MOS-12")
    results.append(check("Privacy: emails scrubbed from free text",
                         1 if "bob@corp.com" not in scrubbed and "[email]" in scrubbed else 0, 1, tol=0))
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mitigation_agent.py")).read()
    wired = src.count("sanitize_issue_for_prompt(") >= 2  # sprint + next-sprint prompt builders
    restored = src.count("restore_aliases(") >= 2  # followup + sprint-plan/report restores
    deep = "deep_pseudonymize(risks[:5]" in src and "deep_pseudonymize(mitigations[:5]" in src
    results.append(check("Privacy: sanitization wired at all prompt sites + followup restore + report deep-scan",
                         1 if (wired and restored and deep) else 0, 1, tol=0))
    # AI fallback payloads must carry a classified reason so the UI can
    # explain the substitution instead of a generic warning.
    results.append(check("Privacy/AI: fallback payloads classify reason for UI",
                         1 if src.count('"fallback_reason"') >= 2 and "_fallback_reason" in src else 0, 1, tol=0))

    # Round-trip: no real names leave for the LLM; aliases restore exactly.
    from prompt_privacy import deep_scrub_text, deep_pseudonymize, restore_aliases

    rt_mapping = {}
    rt_risks = [
        {"type": "STORY_NOT_PROGRESSING", "issue_key": "MOS-1", "assignee": "Sarah Chen",
         "recommendation": "Check with Sarah Chen for blockers on MOS-1."},
        {"type": "BURNDOWN_BEHIND", "issue_key": None, "assignee": "Ravi Patel",
         "recommendation": "Ping Ravi about scope. Escalate to bob@corp.com."},
    ]
    rt_clean = deep_scrub_text(deep_pseudonymize(rt_risks, rt_mapping), rt_mapping)
    rt_json = json.dumps(rt_clean)
    rt_ok = ("Sarah Chen" not in rt_json and "Ravi Patel" not in rt_json
             and "bob@corp.com" not in rt_json and "dev-01" in rt_json and "dev-02" in rt_json)
    results.append(check("Privacy: risks prompt payload carries zero real names/emails",
                         1 if rt_ok else 0, 1, tol=0))
    sample_out = "dev-01 should pair with dev-02 today; DEV-01 owns follow-up."
    rt_back = restore_aliases(sample_out, rt_mapping)
    rt_restore_ok = ("Sarah Chen" in rt_back and "Ravi Patel" in rt_back
                     and "dev-01" not in rt_back and "dev-02" not in rt_back)
    results.append(check("Privacy: alias restore is case-insensitive and complete",
                         1 if rt_restore_ok else 0, 1, tol=0))
    sprint_restored = "restore_aliases(response.text, prompt_mapping)" in src
    report_restored = "restore_aliases(response.text, report_mapping)" in src
    results.append(check("Privacy: sprint plan + stakeholder report restore real names before display",
                         1 if (sprint_restored and report_restored) else 0, 1, tol=0))

    # ------------------------------------------------------------------ #
    # STORY_NOT_PROGRESSING (sprint-clamped staleness)
    # ------------------------------------------------------------------ #
    print("\nSTORY_NOT_PROGRESSING")
    # MOS Sprint 5 scenario: sprint started 30h ago; ticket last touched
    # 14 days ago (pre-sprint). Staleness must clamp to ~30h, not 336h.
    sp = _sprint(days_elapsed=1.25, duration=5, name="MOS Sprint 5")
    old_ticket = _issue("MOS-1", "In Progress", 5, 24 * 14)
    stale_risks = eng.detect_story_progress_risks(sp, [old_ticket], {})
    if not stale_risks:
        raise AssertionError("STORY_NOT_PROGRESSING: pre-sprint-stale ticket did not fire")
    s0 = stale_risks[0]
    results.append(check("Story: pre-sprint silence clamps to sprint age (~30h)",
                         1 if abs(s0["hours_since_update"] - 30) <= 2 else 0, 1, tol=0))
    results.append(check("Story: clamped hours reflected in recommendation",
                         1 if f"no updates in {int(round(s0['hours_since_update']))}h" in s0["recommendation"] else 0, 1, tol=0))

    fresh = _issue("MOS-2", "In Progress", 3, 10)  # updated 10h ago, mid-sprint
    results.append(check("Story: mid-sprint fresh ticket stays silent",
                         len(eng.detect_story_progress_risks(sp, [fresh], {})), 0, tol=0))

    # Same pre-sprint-stale ticket in a week-old sprint: more in-sprint
    # silence => strictly higher score.
    sp_week = _sprint(days_elapsed=7, duration=10, name="MOS Sprint 5")
    week_risks = eng.detect_story_progress_risks(sp_week, [old_ticket], {})
    results.append(check("Story: staleness/score grow with sprint age",
                          1 if week_risks and week_risks[0]["risk_score"] > s0["risk_score"] else 0, 1, tol=0))

    # ------------------------------------------------------------------ #
    # SPRINT_NOT_STARTED (sprint-level: active but 0 In Progress)
    # ------------------------------------------------------------------ #
    print("\nSPRINT_NOT_STARTED")
    # Sprint started 3 days ago, every open ticket still in To Do.
    sp_ns = _sprint(days_elapsed=3, duration=14, name="MOS Sprint 6")
    todo_only = [
        _issue("MOS-1", "To Do", 3, 10),
        _issue("MOS-2", "To Do", 5, 10),
        _issue("MOS-3", "To Do", 2, 10),
    ]
    ns_risks = eng.detect_sprint_no_progress(sp_ns, todo_only, {})
    r = _first(ns_risks, "SPRINT_NOT_STARTED")
    results.append(check("NoProgress: active sprint, all To Do fires",
                         1 if r else 0, 1, tol=0))
    results.append(check("NoProgress: severity MEDIUM (>=20)",
                         1 if r and r["severity"] == "MEDIUM" else 0, 1, tol=0))

    # Counter: a sprint with at least one In Progress ticket must stay silent.
    started = [_issue("MOS-4", "In Progress", 3, 10)] + todo_only
    results.append(check("NoProgress: one started ticket silences",
                         len(eng.detect_sprint_no_progress(sp_ns, started, {})), 0, tol=0))

    # Counter: within the grace window (day 1) must not fire.
    results.append(check("NoProgress: grace window (day 1) silent",
                         len(eng.detect_sprint_no_progress(_sprint(days_elapsed=1, duration=14), todo_only, {})), 0, tol=0))

    # Counter: a not-yet-started (future) sprint must not fire.
    results.append(check("NoProgress: pre-start sprint silent",
                         len(eng.detect_sprint_no_progress(_sprint(days_elapsed=-2, duration=14), todo_only, {})), 0, tol=0))

    # ------------------------------------------------------------------ #
    # SCOPE_CREEP
    # ------------------------------------------------------------------ #
    print("\nSCOPE_CREEP")
    sprint = _sprint(2, 10)
    # Planning committed 3 SP; after start PFIN-1 was re-estimated to 5 SP.
    baseline = {
        "total_sp": 3,
        "issues": {"PFIN-1": 3},
        "captured_at": "2026-01-01T09:00:00",
        "late_capture": False,
    }
    context = {"scope_baseline": baseline, "scope_history": [3, 5]}
    creep = eng.detect_scope_creep(sprint, [_issue("PFIN-1", "In Progress", 5, 4)], context)
    if not creep:
        raise AssertionError("SCOPE_CREEP: estimate hike 3->5 did not trigger")
    c = creep[0]
    results.append(check("Scope: type + growth percent", 1 if c["type"] == "SCOPE_CREEP" and abs(c["growth_percent"] - 66.7) < 0.5 else 0, 1, tol=0))
    results.append(check("Scope: matrix P x I (67% growth, creep already happened)", c["matrix_value"], 25, tol=0))
    results.append(check("Scope: any triggered creep is CRITICAL (>= floor)",
                         1 if c["risk_score"] >= _settings.scope_creep_floor_score and c["severity"] == "CRITICAL" else 0,
                         1, tol=0))
    results.append(check("Scope: floored severity is CRITICAL", 1 if c["severity"] == "CRITICAL" else 0, 1, tol=0))
    results.append(check("Scope: full-confidence baseline", c["confidence"], 75, tol=0))

    late_ctx = {"scope_baseline": dict(baseline, late_capture=True), "scope_history": [3, 5]}
    late = eng.detect_scope_creep(sprint, [_issue("PFIN-1", "In Progress", 5, 4)], late_ctx)
    results.append(check("Scope: late-captured baseline lowers confidence",
                         late[0]["confidence"] if late else 0, 60, tol=0))

    quiet_ctx = {"scope_baseline": baseline, "scope_history": [3, 3]}
    quiet = eng.detect_scope_creep(sprint, [_issue("PFIN-1", "In Progress", 3, 4)], quiet_ctx)
    results.append(check("Scope: no growth/hikes/additions => silent", len(quiet), 0, tol=0))

    cold_ctx = {"scope_baseline": baseline}  # single sync so far
    cold = eng.detect_scope_creep(sprint, [_issue("PFIN-1", "In Progress", 9, 4)], cold_ctx)
    results.append(check("Scope: needs >=2 history points before judging", len(cold), 0, tol=0))

    # Added-issue path: new key absent from the baseline issue map must
    # trigger without raising (regression: NameError in `added` builder).
    add_ctx = {"scope_baseline": {"total_sp": 3, "issues": {}, "captured_at": "2026-01-01T09:00:00"},
               "scope_history": [3, 5]}
    added_risk = eng.detect_scope_creep(sprint, [_issue("PFIN-9", "In Progress", 5, 4)], add_ctx)
    results.append(check("Scope: post-baseline added issue triggers risk",
                         1 if added_risk and added_risk[0]["type"] == "SCOPE_CREEP"
                         and added_risk[0]["added_issues"][0]["key"] == "PFIN-9" else 0, 1, tol=0))
    if added_risk:
        a = added_risk[0]
        results.append(check("Scope: single +5SP addition still floors red",
                             1 if a["severity"] == "CRITICAL" and a["risk_score"] >= _settings.scope_creep_floor_score else 0,
                             1, tol=0))

    src_main = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")).read()
    captures = "baselines[name] = {" in src_main and "late_capture" in src_main
    persists = '"scope_meta": scope_meta' in src_main.replace("'", '"') or 'scope_meta=scope_meta' in src_main
    results.append(check("Scope: auto-baseline on first active sync persisted via snapshot",
                         1 if (captures and persists) else 0, 1, tol=0))

    seeded = "scope-baseline" in src_main and '"manual": True' in src_main.replace("'", '"')
    diag = "🔭 Scope[" in src_main
    results.append(check("Scope: manual seed endpoint + per-sync diagnostic log",
                         1 if (seeded and diag) else 0, 1, tol=0))

    src_snap = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot.py")).read()
    results.append(check("Scope: radar cards aggregate SCOPE_CREEP (SPRINT_LEVEL_RISK_TYPES)",
                         1 if "SCOPE_CREEP" in src_snap.split("SPRINT_LEVEL_RISK_TYPES = ")[1].split("]")[0] else 0,
                         1, tol=0))

    # ------------------------------------------------------------------ #
    print("\n" + "=" * 40)
    passed = sum(results)
    print(f"RESULT: {passed}/{len(results)} checks passed")
    return passed == len(results)


if __name__ == "__main__":
    import sys

    sys.exit(0 if run() else 1)