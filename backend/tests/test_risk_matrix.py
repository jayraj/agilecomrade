from mitigation_agent import filter_false_external_deps
from risk_components import bucket_severity, has_dependency_signal
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
    assert "proactively assess" in plan["owner"]
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
