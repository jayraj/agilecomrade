import inspect
import re

from mitigation_agent import filter_false_external_deps
from risk_components import bucket_severity, has_dependency_signal
from snapshot import _build_radar_data
from risk_matrix import (
    IMPACT_LABELS,
    MATRIX_BANDS,
    PROBABILITY_LABELS,
    bug_i,
    bug_p,
    due_date_i,
    due_date_p,
    external_dep_i,
    external_dep_p,
    matrix_severity,
    overload_i,
    overload_p,
    project_matrix,
    score_from_matrix,
    story_stalled_i,
    story_stalled_p,
)


def test_projection_is_band_aligned() -> None:
    for v in range(1, 26):
        assert bucket_severity(project_matrix(v)) == matrix_severity(v), v


def test_projection_anchors() -> None:
    assert project_matrix(1) == 0
    assert project_matrix(4) == 19
    assert project_matrix(5) == 20
    assert project_matrix(9) == 59
    assert project_matrix(10) == 60
    assert project_matrix(14) == 79
    assert project_matrix(15) == 80
    assert project_matrix(25) == 100


def test_projection_is_monotonic() -> None:
    scores = [project_matrix(v) for v in range(1, 26)]
    assert scores == sorted(scores)


def test_matrix_bands() -> None:
    assert matrix_severity(1) == "LOW"
    assert matrix_severity(4) == "LOW"
    assert matrix_severity(5) == "MEDIUM"
    assert matrix_severity(9) == "MEDIUM"
    assert matrix_severity(10) == "HIGH"
    assert matrix_severity(14) == "HIGH"
    assert matrix_severity(15) == "CRITICAL"
    assert matrix_severity(25) == "CRITICAL"
    assert MATRIX_BANDS[-1][1] == "CRITICAL"


def test_score_from_matrix_clamps_and_projects() -> None:
    r = score_from_matrix(5, 4)
    assert r["probability"] == 5
    assert r["impact"] == 4
    assert r["matrix_value"] == 20
    assert r["severity"] == "CRITICAL"
    assert r["risk_score"] == project_matrix(20) == 90


def test_score_from_matrix_clamps_scale() -> None:
    r = score_from_matrix(9, 0)
    assert r["probability"] == 5
    assert r["impact"] == 1
    assert r["matrix_value"] == 5
    assert r["severity"] == "MEDIUM"


def test_labels_cover_scale() -> None:
    assert set(PROBABILITY_LABELS) == {1, 2, 3, 4, 5}
    assert set(IMPACT_LABELS) == {1, 2, 3, 4, 5}


def test_story_stalled_p_is_monotonic_in_hours() -> None:
    scores = [story_stalled_p(h) for h in (25, 48, 96, 168, 336)]
    assert scores == sorted(scores)
    assert scores[0] == 2
    assert scores[-1] == 5


def test_blocking_bump_raises_impact_one_step() -> None:
    assert story_stalled_i(5, False) == 4
    assert story_stalled_i(5, True) == 5
    assert external_dep_i(2, False) == 3
    assert external_dep_i(2, True) == 4


def test_due_date_p_increases_with_breach() -> None:
    assert due_date_p(1) == 3
    assert due_date_p(4) == 4
    assert due_date_p(9) == 5
    assert [due_date_p(d) for d in (1, 3, 7, 10)] == sorted(
        [due_date_p(d) for d in (1, 3, 7, 10)]
    )


def test_due_date_i_from_size() -> None:
    assert due_date_i(1) == 2
    assert due_date_i(5) == 4
    assert due_date_i(8) == 5


def test_external_p_external_outranks_internal() -> None:
    assert external_dep_p("external") == 4
    assert external_dep_p("internal") == 3
    assert external_dep_p("default") == 3


def test_bug_impact_ordered_by_tier() -> None:
    assert [bug_i(t) for t in ("P1", "P2", "P3", "P4")] == [5, 4, 3, 2]


def test_bug_p_open_aged_fresh_fixed_and_escaped() -> None:
    assert bug_p("P1", False, 0.1) == 3
    assert bug_p("P1", False, 0.9) == 4
    assert bug_p("P1", True, 0.9) == 2
    assert bug_p("P2", False, 0.9) == 2
    assert bug_p("P1", False, 0.9, escaped=True) == 5


def test_dependency_signal_ignores_descriptive_external_mentions() -> None:
    benign = {"key": "PFIN-20", "description": "export my expenses in external tools"}
    assert has_dependency_signal(benign) is False
    blocked_link = {"key": "PFIN-21", "blocked_by": "VENDOR-9", "description": "CSV export"}
    assert has_dependency_signal(blocked_link) is True
    blocked_words = {"key": "PFIN-22", "description": "Blocked: waiting for vendor credentials"}
    assert has_dependency_signal(blocked_words) is True
    depends = {"key": "PFIN-23", "description": "Depends on the Platform team's API"}
    assert has_dependency_signal(depends) is True


def test_ai_external_dep_postfilter_drops_unsupported_keys() -> None:
    issues = [
        {"key": "PFIN-20", "description": "export my expenses in external tools"},
        {"key": "PFIN-30", "description": "Blocked: waiting for vendor credentials"},
    ]
    ai_risks = [
        # supported: the ticket really is blocked
        {"type": "EXTERNAL_DEPENDENCY", "issue_keys": ["PFIN-30"], "count": 1, "risk_score": 70},
        # unsupported: descriptive "external tools" mention only
        {"type": "EXTERNAL_DEPENDENCY", "issue_keys": ["PFIN-20"], "count": 1, "risk_score": 70},
    ]
    kept = filter_false_external_deps(ai_risks, issues)
    assert len(kept) == 1
    assert kept[0]["issue_keys"] == ["PFIN-30"]
    assert kept[0]["issue_key"] == "PFIN-30"


def test_ai_external_dep_postfilter_passes_through_other_types() -> None:
    issues = [{"key": "PFIN-20", "description": "export my expenses in external tools"}]
    ai_risks = [
        {"type": "UNASSIGNED", "issue_keys": ["PFIN-20"], "count": 1, "risk_score": 60},
        {"type": "EXTERNAL_DEPENDENCY", "issue_keys": [], "count": 0, "risk_score": 40},
    ]
    kept = filter_false_external_deps(ai_risks, issues)
    assert [r["type"] for r in kept] == ["UNASSIGNED", "EXTERNAL_DEPENDENCY"]


def test_aggregate_is_band_aligned_to_worst_finding() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    # Two MEDIUM findings (35) blend to ~42 -> MEDIUM, never jump to a higher band.
    assert bucket_severity(eng.aggregate_risk_score([
        {"risk_score": 35, "severity": "MEDIUM"},
        {"risk_score": 35, "severity": "MEDIUM"},
    ])) == "MEDIUM"
    # A HIGH finding floors the aggregate into the HIGH band even after others dilute it.
    agg = eng.aggregate_risk_score([
        {"risk_score": 65, "severity": "HIGH"},
        {"risk_score": 30, "severity": "MEDIUM"},
        {"risk_score": 30, "severity": "MEDIUM"},
        {"risk_score": 30, "severity": "MEDIUM"},
    ])
    assert bucket_severity(agg) == "HIGH"
    # No risks -> 0.
    assert eng.aggregate_risk_score([]) == 0


def test_sprint_field_object_is_not_a_dependency() -> None:
    from jira_fetcher import JiraFetcher
    # The Jira "Sprint" field (customfield_10020) is not a blocker link. With
    # only sprint data present, no blockers are extracted, so the ticket is
    # not flagged as an external dependency.
    fields = {
        "customfield_10020": [{"id": 350, "name": "PFIN Sprint 9", "state": "future", "boardId": 232}],
        "issuelinks": [],
    }
    assert JiraFetcher._blocked_by_keys(fields) == []
    issue = {"key": "PFIN-20", "status": "To Do", "blocked_by": JiraFetcher._blocked_by_keys(fields)}
    assert has_dependency_signal(issue) is False


def test_blocked_by_extracted_from_issue_links() -> None:
    from jira_fetcher import JiraFetcher
    fields = {
        "issuelinks": [
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "outwardIssue": {"key": "PFIN-10"},
            },
            # this ticket blocks the other one -> not a blocker of this ticket
            {
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "inwardIssue": {"key": "PFIN-30"},
            },
        ]
    }
    assert JiraFetcher._blocked_by_keys(fields) == ["PFIN-10"]


def test_detector_fires_only_on_real_blocked_by_link() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    issues = [
        {"key": "PFIN-20", "status": "To Do", "story_points": 3, "blocked_by": ["PFIN-10"]},
        {"key": "PFIN-21", "status": "To Do", "story_points": 3, "blocked_by": []},
    ]
    risks = eng.detect_external_dependencies(issues)
    assert [r["issue_key"] for r in risks] == ["PFIN-20"]
    assert risks[0]["dependency_detail"] == "PFIN-10"


def test_overload_ladders_are_monotonic_and_in_scale() -> None:
    ratios = [1.0, 1.5, 1.9, 2.5, 4.0]
    levels = [overload_p(r) for r in ratios]
    assert levels == sorted(levels)
    assert all(1 <= p <= 5 for p in levels)
    sps = [0, 6, 10, 20, 40]
    impacts = [overload_i(sp) for sp in sps]
    assert impacts == sorted(impacts)
    assert all(1 <= i <= 5 for i in impacts)


def test_overload_probability_steps_at_the_approved_thresholds() -> None:
    assert overload_p(1.5) == 3
    assert overload_p(1.9) == 3
    assert overload_p(2.0) == 4
    assert overload_p(2.9) == 4
    assert overload_p(3.0) == 5
    assert overload_p(9.9) == 5


def test_detect_overload_flags_disproportionate_assignee() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    issues = [
        {"key": "PFIN-20", "status": "To Do", "assignee": "dev-03", "story_points": 3},
        {"key": "PFIN-19", "status": "To Do", "assignee": "dev-03", "story_points": 3},
        {"key": "PFIN-15", "status": "To Do", "assignee": "dev-03", "story_points": 2},
        {"key": "PFIN-21", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-23", "status": "To Do", "assignee": "dev-02", "story_points": 3},
    ]
    risks = eng.detect_overload(issues)
    assert len(risks) == 1
    risk = risks[0]
    assert risk["type"] == "OVERLOADED"
    assert risk["assignee"] == "dev-03"
    assert risk["count"] == 3
    assert risk["issue_keys"] == ["PFIN-20", "PFIN-19", "PFIN-15"]
    assert risk["total_sp"] == 8
    assert risk["load_ratio"] == 1.8
    assert bucket_severity(risk["risk_score"]) == risk["severity"]
    assert risk["severity"] == "MEDIUM"


def test_detect_overload_stays_quiet_on_balanced_teams() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    # Perfectly even 2/2/2 -> no one is disproportionately loaded.
    issues = [
        {"key": f"PFIN-{n}", "status": "To Do", "assignee": assignee, "story_points": 2}
        for n, assignee in enumerate(["dev-01", "dev-01", "dev-02", "dev-02", "dev-03", "dev-03"])
    ]
    assert eng.detect_overload(issues) == []


def test_detect_overload_needs_a_baseline_and_skips_unassigned() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    # A single assignee gives no team average to compare against.
    solo = [{"key": f"PFIN-{n}", "status": "To Do", "assignee": "dev-01", "story_points": 3} for n in range(5)]
    assert eng.detect_overload(solo) == []
    # Unassigned work is UNASSIGNED's job, and never counts toward the baseline.
    mixed = [
        {"key": "PFIN-20", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-21", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-22", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-23", "status": "To Do", "assignee": "Unassigned", "story_points": 3},
    ]
    assert eng.detect_overload(mixed) == []


def test_detect_overload_ignores_completed_work() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    # dev-01's third ticket is Done, so the load is back to a 2/1 split.
    issues = [
        {"key": "PFIN-20", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-21", "status": "To Do", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-22", "status": "Done", "assignee": "dev-01", "story_points": 3},
        {"key": "PFIN-23", "status": "To Do", "assignee": "dev-02", "story_points": 3},
    ]
    assert eng.detect_overload(issues) == []


def test_overloaded_runs_through_the_next_sprint_pipeline() -> None:
    from risk_engine import RiskEngine
    eng = RiskEngine()
    issues = [
        {"key": "PFIN-20", "status": "To Do", "assignee": "dev-03", "story_points": 3, "due_date": None},
        {"key": "PFIN-19", "status": "To Do", "assignee": "dev-03", "story_points": 3, "due_date": None},
        {"key": "PFIN-15", "status": "To Do", "assignee": "dev-03", "story_points": 2, "due_date": None},
        {"key": "PFIN-21", "status": "To Do", "assignee": "dev-01", "story_points": 3, "due_date": None},
        {"key": "PFIN-23", "status": "To Do", "assignee": "dev-02", "story_points": 3, "due_date": None},
    ]
    risks = eng.calculate_next_sprint_risks(issues)
    overload = [r for r in risks if r.get("type") == "OVERLOADED"]
    assert len(overload) == 1
    assert overload[0]["assignee"] == "dev-03"


def test_overloaded_has_a_dedicated_explainer() -> None:
    from risk_explainer import explain_risk
    risk = {
        "type": "OVERLOADED",
        "assignee": "dev-03",
        "count": 3,
        "issue_keys": ["PFIN-20", "PFIN-19", "PFIN-15"],
        "total_sp": 8,
        "load_ratio": 1.8,
        "team_average": 1.67,
        "risk_score": 59,
        "severity": "MEDIUM",
        "confidence": 75,
        "probability": 3,
        "impact": 3,
        "matrix_value": 9,
        "recommendation": "Reassign at least one ticket from dev-03.",
    }
    explain_risk(risk)
    assert "dev-03" in risk["signal"]["label"]
    assert "3 planned item" in risk["signal"]["label"]
    assert "dev-03" in risk["suspected_cause"]
    assert risk["suggested_action"] == "Reassign at least one ticket from dev-03."
    assert "dev-03" in risk["severity_reason"]


def _offline_agent():
    """A MitigationAgent with no provider, so the call fails fast and locally."""
    from config import UserConfig
    from mitigation_agent import MitigationAgent
    return MitigationAgent(UserConfig())


def test_no_risks_fallback_plan_is_reassurance_not_urgency() -> None:
    agent = _offline_agent()
    plan = agent._generate_sprint_mitigation({
        "sprint_key": "Sprint 9",
        "project_key": "PFIN",
        "risks": [],
        "issues": [],
    })
    assert plan["ai_used"] is False
    assert plan["fallback_reason"] == "not_configured"
    assert "Scrum Master" in plan["owner"]
    assert "no risks detected" in plan["owner"]
    assert "Please proactively keep assessing." in plan["owner"]
    # A comma (not a semicolon) keeps this a single UI bullet, since
    # splitItems() in the frontend breaks owner text on ";".
    assert ";" not in plan["owner"]
    assert plan["timeline"] == "Keep checking (reassess at the next standup)"


def test_risky_fallback_plan_keeps_the_urgent_timeline() -> None:
    agent = _offline_agent()
    plan = agent._generate_sprint_mitigation({
        "sprint_key": "Sprint 9",
        "project_key": "PFIN",
        "risks": [{"type": "BUG_RAISED", "issue_key": "PFIN-20", "risk_score": 70, "confidence": 80}],
        "issues": [],
    })
    assert plan["ai_used"] is False
    assert plan["timeline"] == "ASAP (within 24 hours)"
    assert "ASAP" not in plan["owner"]


# --------------------------------------------------------------------- #
# Ownership + escalation routing
#
# The OWNER field used to degrade to a bare role name ("Scrum Master
# (escalate if needed)"), which is unusable at a standup: no escalation
# target, no trigger, no timebox. These tests pin the three properties that
# make an owner line actionable, for every risk type the engine emits.
# --------------------------------------------------------------------- #

# One representative risk payload per risk type RiskEngine.calculate_all_risks
# can emit, with the diagnostic fields that type actually carries.
_OWNER_SAMPLES: dict = {
    "STORY_NOT_PROGRESSING": {
        "issue_key": "PFIN-101", "hours_since_update": 72.0,
        "status": "In Progress", "risk_score": 80,
    },
    "SPRINT_NOT_STARTED": {"days_elapsed": 3, "open_count": 14, "risk_score": 95},
    "BURNDOWN_BEHIND": {
        "burndown_gap_percent": 32.5, "remaining_sp": 44.0,
        "issue_keys": ["PFIN-1", "PFIN-2"], "risk_score": 95,
    },
    "QA_BOTTLENECK": {
        "qa_stories_count": 9, "stuck_stories": [{"key": "PFIN-3"}], "risk_score": 80,
    },
    "EXTERNAL_DEPENDENCY": {
        "issue_key": "PFIN-9", "dependency_kind": "external", "risk_score": 75,
    },
    "DUE_DATE_PASSED": {"count": 3, "issue_keys": ["PFIN-4", "PFIN-5"], "risk_score": 90},
    "BUG_RAISED": {"issue_key": "PFIN-20", "tier": "P1", "risk_score": 80},
    "SCOPE_CREEP": {
        "growth_percent": 42.0, "baseline_sp": 50, "current_sp": 71, "risk_score": 75,
    },
    "SPRINT_ENDED_INCOMPLETE": {
        "remaining_sp": 30.0, "days_overdue": 4, "risk_score": 95,
    },
    "OVERLOADED": {"count": 6, "load_ratio": 2.4, "risk_score": 75},
}

# Vague escalation phrasing that must never survive in an owner line. The
# owner *subject* being vague is covered separately by
# test_owner_line_names_the_routed_accountable_role.
_VAGUE_OWNER_PHRASES = (
    "escalate if needed",
    "escalate if required",
    "escalate as necessary",
    "if it persists",
    "if required",
    "as necessary",
    "and escalate if",
)


def _owner_line(agent, risk_type: str, **overrides) -> str:
    risk = {"type": risk_type, **_OWNER_SAMPLES[risk_type], **overrides}
    return agent._fallback_owner([risk])


def test_every_emitted_risk_type_has_an_owner_routing() -> None:
    """No risk type may be missing from the routing tables — an unmapped type
    is what let 4 of the 10 fall through to a bare role name."""
    from mitigation_agent import _OWNER_ROLE
    from risk_engine import RiskEngine

    assert set(_OWNER_SAMPLES) == set(_OWNER_ROLE)
    # Belt and braces: if a detector is ever added, this fails until it is routed.
    src = inspect.getsource(RiskEngine)
    emitted = set(re.findall(r'_emit\(\s*\n\s*"([A-Z_]+)"', src))
    assert emitted, "could not detect emitted risk types from source"
    assert emitted <= set(_OWNER_ROLE), emitted - set(_OWNER_ROLE)


def test_owner_line_names_the_routed_accountable_role() -> None:
    from mitigation_agent import _OWNER_ROLE

    agent = _offline_agent()
    for risk_type, role in _OWNER_ROLE.items():
        line = _owner_line(agent, risk_type)
        assert line.startswith(role), (risk_type, line)


def test_owner_line_carries_a_concrete_escalation_path() -> None:
    """Every type with an escalation target must name both the target and an
    observable trigger. SCOPE_CREEP is deliberately exempt: scope is already
    the Product Owner's call, so there is nothing above them to escalate to."""
    from mitigation_agent import _ESCALATION

    agent = _offline_agent()
    for risk_type, (target, _trigger) in _ESCALATION.items():
        line = _owner_line(agent, risk_type)
        if target is None:
            assert "escalate" not in line, (risk_type, line)
            continue
        assert "escalate to " in line, (risk_type, line)
        assert target in line, (risk_type, line)


def test_owner_line_has_urgency_in_the_escalation_trigger() -> None:
    """The trigger after "escalate to" must be observable and time-bound.

    This is the precise fix for "Scrum Master (escalate if needed)": a target
    alone is not enough if the condition for handing it over is open-ended.
    """
    from mitigation_agent import _ESCALATION

    agent = _offline_agent()
    timebound = ("today", "standup", "end of day", "sprint end", "this week",
                 "planning session", "before", "is not in test")
    for risk_type, (target, _trigger) in _ESCALATION.items():
        line = _owner_line(agent, risk_type)
        if target is None:
            assert "escalate" not in line, (risk_type, line)
            continue
        _head, sep, trigger = line.partition(", then escalate to ")
        assert sep, (risk_type, line)
        assert trigger.strip(), (risk_type, line)
        low = line.lower()
        for phrase in _VAGUE_OWNER_PHRASES:
            assert phrase not in low, (risk_type, phrase, line)
        assert any(t in low for t in timebound), (risk_type, line)


def test_owner_line_never_defers_to_an_unnamed_actor() -> None:
    """"hand it to someone" is a vague owner in the same way "escalate if
    needed" is a vague trigger."""
    agent = _offline_agent()
    for risk_type in _OWNER_SAMPLES:
        line = _owner_line(agent, risk_type)
        assert "someone" not in line.lower(), (risk_type, line)
        assert "anyone" not in line.lower(), (risk_type, line)


def test_owner_line_is_free_of_semicolons() -> None:
    """splitItems() in the frontend breaks owner text on ";", so a semicolon
    would shred one action into fragments."""
    agent = _offline_agent()
    for risk_type in _OWNER_SAMPLES:
        assert ";" not in _owner_line(agent, risk_type), risk_type


def test_owner_line_quotes_real_diagnostics_not_just_a_role() -> None:
    """The whole point over a bare role: the line must carry the evidence."""
    agent = _offline_agent()
    assert "PFIN-101" in _owner_line(agent, "STORY_NOT_PROGRESSING")
    assert "72h" in _owner_line(agent, "STORY_NOT_PROGRESSING")
    assert "32.5%" in _owner_line(agent, "BURNDOWN_BEHIND")
    assert "44 SP" in _owner_line(agent, "BURNDOWN_BEHIND")
    assert "P1" in _owner_line(agent, "BUG_RAISED")
    assert "PFIN-9" in _owner_line(agent, "EXTERNAL_DEPENDENCY")
    assert "2.4x" in _owner_line(agent, "OVERLOADED")
    assert "50 → 71 SP" in _owner_line(agent, "SCOPE_CREEP")


def test_owner_fallback_never_degrades_to_a_bare_role_name() -> None:
    """An unmapped detector must still yield a role, an action and no empty
    string — the old code returned None here and the caller substituted a
    bare "Scrum Master"."""
    agent = _offline_agent()
    line = agent._fallback_owner([{"type": "BRAND_NEW_DETECTOR", "risk_score": 50}])
    assert line.startswith("Scrum Master")
    assert "brand new detector" in line
    assert ";" not in line
    assert agent._fallback_owner([]) == ""


def test_owner_merge_keeps_top_two_risks_each_fully_routed() -> None:
    agent = _offline_agent()
    merged = agent._fallback_owner([
        {"type": "BURNDOWN_BEHIND", **_OWNER_SAMPLES["BURNDOWN_BEHIND"], "risk_score": 95},
        {"type": "QA_BOTTLENECK", **_OWNER_SAMPLES["QA_BOTTLENECK"], "risk_score": 80},
        {"type": "BUG_RAISED", **_OWNER_SAMPLES["BUG_RAISED"], "risk_score": 40},
    ])
    assert "burndown" in merged
    assert "QA" in merged
    assert "P1" not in merged  # third-highest is dropped by design
    assert ";" not in merged


def test_extracted_ai_owner_defaults_to_an_actionable_line() -> None:
    """An AI reply with no OWNER section used to fall back to "Scrum Master"."""
    from mitigation_agent import _GENERIC_OWNER_TEXT

    agent = _offline_agent()
    assert agent._extract_owner("no sections here at all") == _GENERIC_OWNER_TEXT
    assert agent._extract_owner("no sections here at all") != "Scrum Master"
    # Real bullets still win, and "; " remains the bullet separator.
    reply = "OWNER:\n- Tech Lead: unblock PFIN-1\n- Product Owner: cut scope\n\nTIMELINE:\n- today"
    assert agent._extract_owner(reply) == "Tech Lead: unblock PFIN-1; Product Owner: cut scope"
    # A semicolon leaked inside a bullet is neutralised, not passed through.
    leaky = "OWNER:\n- Tech Lead: unblock PFIN-1; then re-test"
    assert agent._extract_owner(leaky) == "Tech Lead: unblock PFIN-1, then re-test"


def test_sprint_prompt_forbids_vague_owner_placeholders() -> None:
    """The AI path is non-deterministic, so the prompt contract is what stops
    "escalate if needed" coming back. Pin the instruction itself."""
    from mitigation_agent import MitigationAgent
    from config import UserConfig

    agent = MitigationAgent(UserConfig())
    prompt, _mapping = agent._build_sprint_prompt({
        "sprint_key": "Sprint 9", "project_key": "PFIN", "risks": [], "issues": [],
    })
    assert "escalate to <Role> if <concrete trigger>" in prompt
    assert "escalate if needed" in prompt  # named as the banned phrasing
    assert "semicolons" in prompt
    # The role routing table is in the prompt so the model picks from the same
    # set the rule-based fallback uses.
    from mitigation_agent import _OWNER_ROLE
    for risk_type, role in _OWNER_ROLE.items():
        assert risk_type in prompt, risk_type
        assert role in prompt, role


# --------------------------------------------------------------------- #
# Radar card rollup: a sprint's card must never look cleaner than the
# risks actually detected for it. Regression cover for the type allowlist
# that dropped every ticket-level risk and rendered those sprints as
# 0% / ON_TRACK while their detail pages were full of risks.
# --------------------------------------------------------------------- #

def _sprint_data(name: str = "Sprint 9") -> dict:
    return {
        "PFIN": {
            "sprint": {"name": name, "startDate": "2026-01-01", "endDate": "2026-01-14"},
            "issues": [],
        }
    }


def _risk(risk_type: str, score: float = 49, sprint_key: str | None = "Sprint 9", **extra) -> dict:
    risk = {
        "type": risk_type,
        "sprint_key": sprint_key,
        "issue_key": "PFIN-23",
        "risk_score": score,
        "raw_score": 8,
        "severity": "MEDIUM",
    }
    risk.update(extra)
    return risk


def test_card_aggregates_ticket_level_risks() -> None:
    """A sprint whose only risk is a stalled story must not read 0% / ON_TRACK."""
    for risk_type in (
        "STORY_NOT_PROGRESSING",
        "EXTERNAL_DEPENDENCY",
        "DUE_DATE_PASSED",
    ):
        card = _build_radar_data(_sprint_data(), [_risk(risk_type)])[0]
        assert card["risk_score"] == 49, risk_type
        assert card["risk_types"] == [risk_type], risk_type
        assert card["risk_type"] == risk_type, risk_type


def test_card_aggregates_mixed_risk_types() -> None:
    card = _build_radar_data(_sprint_data(), [
        _risk("STORY_NOT_PROGRESSING", 49),
        _risk("BURNDOWN_BEHIND", 70, severity="HIGH"),
    ])[0]
    assert card["risk_score"] == 70  # the worst risk wins
    assert set(card["risk_types"]) == {"STORY_NOT_PROGRESSING", "BURNDOWN_BEHIND"}


def test_card_still_excludes_risks_without_a_sprint_key() -> None:
    card = _build_radar_data(_sprint_data(), [_risk("STORY_NOT_PROGRESSING", sprint_key=None)])[0]
    assert card["risk_score"] == 0
    assert card["risk_type"] == "ON_TRACK"
    assert card["risk_types"] == []


def test_clean_sprint_still_reports_on_track() -> None:
    card = _build_radar_data(_sprint_data(), [])[0]
    assert card["risk_score"] == 0
    assert card["raw_score"] == 0
    assert card["risk_type"] == "ON_TRACK"
    assert card["severity"] == "LOW"
    assert card["risk_types"] == []


# --------------------------------------------------------------------- #
# Queued-ticket exemption: a not-started ticket whose assignee is already
# mid-flight is waiting its turn, not stalled. Without this, a healthy team
# with more tickets than free hands reported every un-started story as
# STORY_NOT_PROGRESSING. The exemption must lapse as the sprint runs out.
# --------------------------------------------------------------------- #

from datetime import datetime, timedelta, timezone

from risk_engine import RiskEngine


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _sp(days_elapsed: float = 2.0, duration: int = 14) -> dict:
    start = datetime.now(timezone.utc) - timedelta(days=days_elapsed)
    return {
        "name": "PFIN Sprint 9",
        "startDate": start.isoformat(),
        "endDate": (start + timedelta(days=duration)).isoformat(),
    }


def _iss(key, status, assignee, sp, hours=28):
    return {
        "key": key,
        "summary": f"{key} summary",
        "status": status,
        "story_points": sp,
        "assignee": assignee,
        "updated": _iso_days_ago(hours / 24),
        "description": "",
    }


def _four_busy_devs_with_three_queued() -> list:
    """The reported situation: 4 devs mid-flight, 3 stories still To Do.

    The In Progress stories are touched hours ago because those developers are
    actively working; only the queued To Do ones have sat untouched.
    """
    issues = [_iss(f"PFIN-{n}", "In Progress", dev, 5, hours=2) for n, dev in
              zip(range(1, 5), ("dev-1", "dev-2", "dev-3", "dev-4"))]
    issues += [_iss(f"PFIN-{n}", "To Do", dev, 5) for n, dev in
               zip(range(10, 13), ("dev-1", "dev-2", "dev-3"))]
    return issues


def test_queued_ticket_behind_busy_dev_is_not_a_stall() -> None:
    """4 devs busy, 3 stories queued behind them, 28h untouched -> no risk."""
    risks = RiskEngine().detect_story_progress_risks(_sp(), _four_busy_devs_with_three_queued(), {})
    assert risks == []


def test_queued_ticket_resurfaces_when_sprint_runs_out() -> None:
    """With under the grace window left, the same queue can no longer start."""
    # 13 days elapsed of 14 -> days_remaining == 1 < grace of 2.
    risks = RiskEngine().detect_story_progress_risks(
        _sp(days_elapsed=13, duration=14), _four_busy_devs_with_three_queued(), {}
    )
    assert len(risks) == 3
    assert {r["issue_key"] for r in risks} == {"PFIN-10", "PFIN-11", "PFIN-12"}
    assert all(r["type"] == "STORY_NOT_PROGRESSING" for r in risks)


def test_queued_ticket_on_idle_dev_is_still_flagged() -> None:
    """Nobody is working, so an untouched story really is neglect."""
    issues = [_iss("PFIN-10", "To Do", "dev-1", 5)]
    risks = RiskEngine().detect_story_progress_risks(_sp(), issues, {})
    assert len(risks) == 1
    assert risks[0]["issue_key"] == "PFIN-10"


def test_started_but_silent_ticket_is_never_exempt() -> None:
    """The exemption is for un-started work only; a started stall still fires."""
    issues = [
        _iss("PFIN-1", "In Progress", "dev-1", 5),
        _iss("PFIN-10", "To Do", "dev-1", 5),
    ]
    risks = RiskEngine().detect_story_progress_risks(_sp(), issues, {})
    assert [r["issue_key"] for r in risks] == ["PFIN-1"]


def test_only_the_idle_devs_queued_ticket_is_flagged() -> None:
    """One busy dev, one idle dev: only the idle dev's story is a real risk."""
    issues = [
        _iss("PFIN-1", "In Progress", "busy-dev", 5, hours=2),
        _iss("PFIN-2", "To Do", "busy-dev", 5),
        _iss("PFIN-3", "To Do", "idle-dev", 5),
    ]
    risks = RiskEngine().detect_story_progress_risks(_sp(), issues, {})
    assert [r["issue_key"] for r in risks] == ["PFIN-3"]


def test_unassigned_queued_ticket_is_never_exempt() -> None:
    """'Unassigned' is not a busy developer, so the gate cannot hide it."""
    issues = [_iss("PFIN-1", "In Progress", "dev-1", 5, hours=2), _iss("PFIN-9", "To Do", "Unassigned", 5)]
    risks = RiskEngine().detect_story_progress_risks(_sp(), issues, {})
    assert [r["issue_key"] for r in risks] == ["PFIN-9"]
