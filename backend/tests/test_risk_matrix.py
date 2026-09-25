from risk_components import bucket_severity
from risk_matrix import (
    IMPACT_LABELS,
    MATRIX_BANDS,
    PROBABILITY_LABELS,
    matrix_severity,
    project_matrix,
    score_from_matrix,
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
