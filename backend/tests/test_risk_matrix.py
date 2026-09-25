from risk_components import bucket_severity
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
