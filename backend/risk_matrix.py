"""Standard 5x5 Probability x Impact risk matrix.

The model is deliberately small and self-contained:

    probability P : 1..5   how likely is this risk to crystallise?
    impact      I : 1..5   how bad is it if it does?
    matrix_value = P x I   (1..25)

    severity = matrix band of (P x I)
    risk_score = project_matrix(P x I)   # band-aligned 0..100

`project_matrix` is a piecewise-linear map from 1..25 onto 0..100 whose
anchors land exactly on the legacy 0-100 severity boundaries (19/20/59/60/79/80).
Because it never crosses a band, `bucket_severity(project_matrix(v))` always
equals the matrix band, so the existing 0-100 UI, gauge, RAG thresholds and
frontend `RISK_BANDS` keep working unchanged.

P and I are inferred from Jira telemetry (an automated proxy for expert
assessment, not a replacement for it). Every ladder is an explicit, ordered
threshold table so calibration is auditable and testable.
"""
from config import settings
from risk_components import bucket_severity

# Severity bands over the 1..25 matrix product. These are this app's own
# calibration, tuned for sprint risk — they are not prescribed by any external
# standard.
MATRIX_BANDS = (
    (4, "LOW"),
    (9, "MEDIUM"),
    (14, "HIGH"),
    (25, "CRITICAL"),
)

PROBABILITY_LABELS = {1: "Rare", 2: "Unlikely", 3: "Possible", 4: "Likely", 5: "Almost Certain"}
IMPACT_LABELS = {1: "Insignificant", 2: "Minor", 3: "Moderate", 4: "Major", 5: "Critical"}

MIN_SCALE = 1
MAX_SCALE = 5


def _clamp(value, low, high):
    return max(low, min(high, value))


def _ladder_up(value, steps, default):
    """First `(threshold, level)` whose threshold is >= value, else `default`."""
    for threshold, level in steps:
        if value <= threshold:
            return level
    return default


def _ladder_down(value, steps, default):
    """First `(threshold, level)` whose threshold is <= value, else `default`."""
    for threshold, level in steps:
        if value >= threshold:
            return level
    return default


def matrix_severity(matrix_value):
    """Severity band of a 1..25 matrix product."""
    for ceiling, band in MATRIX_BANDS:
        if matrix_value <= ceiling:
            return band
    return MATRIX_BANDS[-1][1]


def project_matrix_float(matrix_value):
    """Continuous band-aligned projection of a 1..25 matrix product to 0..100.

    Anchors: 1->0, 4->19, 5->20, 9->59, 10->60, 14->79, 15->80, 25->100.
    """
    v = _clamp(matrix_value, 1, 25)
    if v <= 4:
        return 0 + (v - 1) / 3 * 19
    if v <= 9:
        return 20 + (v - 5) / 4 * 39
    if v <= 14:
        return 60 + (v - 10) / 4 * 19
    return 80 + (v - 15) / 10 * 20


def project_matrix(matrix_value):
    """Rounded 0..100 display score for a 1..25 matrix product."""
    return int(round(project_matrix_float(matrix_value)))


def score_from_matrix(probability, impact):
    """Build the canonical scoring block from a (P, I) pair.

    `raw_score` is the continuous projection (on the same 0..100 scale as the
    legacy pipeline, so mixed-mode ranking stays comparable) and `risk_score`
    is its rounded form.
    """
    p = int(_clamp(round(probability), MIN_SCALE, MAX_SCALE))
    i = int(_clamp(round(impact), MIN_SCALE, MAX_SCALE))
    matrix_value = p * i
    raw = project_matrix_float(matrix_value)
    return {
        "probability": p,
        "impact": i,
        "matrix_value": matrix_value,
        "severity": matrix_severity(matrix_value),
        "risk_score": int(round(raw)),
        "raw_score": round(raw, 1),
    }


# --------------------------------------------------------------------------- #
# Probability derivations: "will this crystallise before the sprint ends?"
# --------------------------------------------------------------------------- #

def sprint_not_started_p(elapsed_fraction):
    """Nothing has started: the longer the sprint has run, the more certain the miss."""
    return _ladder_up(elapsed_fraction, ((0.33, 3), (0.66, 4)), 5)


def sprint_not_started_i(open_count):
    return _ladder_up(open_count, ((2, 2), (5, 3), (10, 4)), 5)


def _trend_delta(trend):
    """Map `trend_factor` output to a probability nudge."""
    if trend is None:
        return 0
    if trend <= settings.trend_fast + 0.001:
        return -1
    if trend >= settings.trend_flat - 0.001:
        return 1
    return 0


def burndown_p(days_left_fraction, trend):
    """Can the gap still close? urgency from time left, nudged by the gap trend."""
    base = _ladder_down(days_left_fraction, ((0.5, 2), (0.25, 3), (0.1, 4)), 5)
    return int(_clamp(base + _trend_delta(trend), MIN_SCALE, MAX_SCALE))


def burndown_i(gap_pct):
    return _ladder_up(gap_pct, ((15, 2), (30, 3), (50, 4)), 5)


def qa_p(clear_ratio):
    """QA throughput: can the queue clear before the sprint ends?"""
    return _ladder_up(clear_ratio, ((0.5, 1), (1.0, 2), (1.5, 3), (2.5, 4)), 5)


def qa_i(queue_count, stuck_count=0):
    base = _ladder_up(queue_count, ((2, 2), (4, 3), (7, 4)), 5)
    if stuck_count:
        base += 1
    return int(_clamp(base, MIN_SCALE, MAX_SCALE))


# --------------------------------------------------------------------------- #
# Impact derivations: "how much exposure if it bites?"
# --------------------------------------------------------------------------- #

def scope_creep_p():
    """Scope has already changed after kickoff: this is a fact, not a probability."""
    return 5


def scope_creep_i(growth_pct, added_or_hiked=False):
    base = _ladder_up(growth_pct, ((10, 2), (25, 3), (50, 4)), 5)
    if added_or_hiked:
        base = max(base, 3)
    return base


def sprint_ended_p():
    """The sprint already ended: the risk has materialised."""
    return 5


def sprint_ended_i(remaining_sp, total_sp):
    if remaining_sp is None or remaining_sp <= 0:
        return 1  # nothing left undone; this is only a housekeeping miss
    fraction = (remaining_sp / total_sp) if total_sp else 1.0
    return _ladder_up(fraction, ((0.1, 2), (0.25, 3), (0.5, 4)), 5)


# --------------------------------------------------------------------------- #
# Assignee capacity ladders: "is one person carrying the sprint?"
# --------------------------------------------------------------------------- #

def overload_p(ratio):
    """How concentrated is one person's load relative to the team average?

    Uses a down-ladder so each step is a floor: >=1.5x is MEDIUM-leaning (P3),
    >=2x is HIGH-leaning (P4), >=3x is CRITICAL-leaning (P5).
    """
    return _ladder_down(ratio, ((3.0, 5), (2.0, 4), (1.5, 3)), 3)


def overload_i(total_sp):
    """How much work is stranded on that person if the load does not spread?"""
    return _ladder_up(total_sp, ((6, 2), (12, 3), (24, 4)), 5)


# --------------------------------------------------------------------------- #
# Ticket-level ladders
# --------------------------------------------------------------------------- #

def _size_i(story_points):
    """Impact of an undelivered ticket from its size alone."""
    return _ladder_up(story_points, ((1, 2), (3, 3), (5, 4)), 5)


def _size_and_blocking_i(story_points, blocks_others):
    """Ticket impact from size, bumped one step when it blocks other work."""
    base = _size_i(story_points)
    if blocks_others:
        base += 1
    return int(_clamp(base, MIN_SCALE, MAX_SCALE))


def story_stalled_p(hours_stale):
    """Will a silent ticket slip? Longer in-sprint silence = more certain."""
    return _ladder_down(hours_stale, ((168, 5), (96, 4), (48, 3)), 2)


def story_stalled_i(story_points, blocks_others):
    return _size_and_blocking_i(story_points, blocks_others)


def external_dep_p(kind):
    """Will the dependency bite? External procurement is the least controllable."""
    return 4 if kind == "external" else 3


def external_dep_i(story_points, blocks_others):
    return _size_and_blocking_i(story_points, blocks_others)


def due_date_p(days_overdue):
    """The date has passed; deeper breach = more certain to hurt the sprint."""
    return _ladder_down(days_overdue, ((7, 5), (3, 4), (1, 3)), 3)


def due_date_i(story_points):
    return _size_i(story_points)


def bug_i(tier):
    """Defect severity tier -> impact (P1=Critical .. P4=Minor)."""
    return {"P1": 5, "P2": 4, "P3": 3, "P4": 2}.get(tier, 3)


def bug_p(tier, is_done_flag, age_fraction, escaped=False):
    """Will the defect impair the sprint? Open P1s worsen with age; fixed P1s are contained."""
    if escaped:
        return 5
    if tier == "P1":
        if is_done_flag:
            return 2
        return 4 if age_fraction > 0.5 else 3
    return 2


def assert_projection_is_band_aligned():
    """Guard: every 1..25 product must project into its own severity band."""
    for v in range(1, 26):
        projected = project_matrix(v)
        if bucket_severity(projected) != matrix_severity(v):
            raise AssertionError(
                f"projection misaligned at matrix_value={v}: "
                f"projected={projected} -> {bucket_severity(projected)}, "
                f"matrix band={matrix_severity(v)}"
            )
