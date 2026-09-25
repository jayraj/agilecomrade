# Sprint Risk Engine — Scoring & Severity Review

A deep-dive into how the risk engine (`backend/risk_engine.py`, `backend/risk_components.py`, and
`backend/risk_matrix.py`) detects sprint risks, computes each score, and maps it to severity. Every
rule is pinned by the worked examples in `backend/validate_rubric.py` (`python3 validate_rubric.py`).

> **Key mental model:** every risk emits two numbers — `risk_score` (0–100, drives the UI gauge and
> severity) and `raw_score` (the continuous projection, for ranking/triage) — plus its `probability`,
> `impact` and `matrix_value` for explainability. All detectors keep the same *triggers* as v1; only
> the scoring changed.
>
> **The engine runs one model: a standard 5×5 Probability × Impact matrix (ISO 31005 style).** Every
> detector — sprint-level and ticket-level — derives an ordinal `P` and `I` from telemetry and scores
> `P × I`. The old `base × ∏multipliers` product model and the defect band model have both been
> retired. See §2.9 and `backend/risk_matrix.py` for the model and the per-detector ladders.

---

## 1. Architecture overview

`RiskEngine.calculate_all_risks` (risk_engine.py:88) loops over every project's active sprint and runs
**nine detectors** per sprint. Each detector runs in isolation (risk_engine.py:108–129): if one raises,
it is logged and skipped while the rest still run, so a single rule can never blank the panel.

| # | Detector | Emitted risk type | Level |
|---|----------|-------------------|-------|
| 1 | `detect_story_progress_risks` | `STORY_NOT_PROGRESSING` | ticket |
| 1b | `detect_sprint_no_progress` | `SPRINT_NOT_STARTED` | sprint |
| 2 | `detect_burndown_risks` | `BURNDOWN_BEHIND` | sprint |
| 3 | `detect_qa_bottleneck` | `QA_BOTTLENECK` | sprint |
| 4 | `detect_external_dependencies` | `EXTERNAL_DEPENDENCY` | ticket |
| 5 | `detect_due_date_risks` | `DUE_DATE_PASSED` | sprint |
| 6 | `detect_bug_risks` | `BUG_RAISED` | ticket |
| 7 | `detect_scope_creep` | `SCOPE_CREEP` | sprint |
| 8 | `detect_sprint_overdue_risk` | `SPRINT_ENDED_INCOMPLETE` | sprint |

Risks come back sorted by `raw_score` descending, so the most severe (highest-projected) risk is always
first. The resolved list is enriched by `risk_explainer.py` (signal → suspected cause → suggested
action, plus a `P×I` `severity_reason` sentence and a stable `risk_id`) and assembled into the snapshot
by `snapshot.py`.

---

## 2. Shared scoring components

Every detector leans on the same table-driven helpers in `risk_components.py`, all tunable from
`config.py`. **Current role after the matrix migration:** `trend_factor` is the only one still on the
scoring path (it feeds `BURNDOWN_BEHIND`'s probability via a ±1 nudge, §2.9). `workflow_stage_weight`,
`size_weight` and `assignee_factor` are retained as *diagnostic* fields on the risk payload (and the
matrix's ticket-impact size ladder is a separate ordinal table, §2.9). `time_pressure_multiplier` is
retained only because the rubric asserts its curve; no detector multiplies by it anymore.

### 2.1 Time-pressure multiplier (`time_pressure_multiplier`, risk_components.py:81)
The same magnitude of risk is worse the closer the sprint is to its end. It is a step function of
`pct_sprint_elapsed = days_elapsed / duration` (config.py:44):

| Sprint elapsed ≤ | Multiplier |
|:---|:---|
| 25% | 0.6 |
| 50% | 0.8 |
| 75% | 1.1 |
| 90% | 1.4 |
| 100%+ | 1.7 |

### 2.2 Workflow-stage weight (`workflow_stage_weight`, risk_components.py:90)
Effort already sunk into a ticket makes losing it more costly. Unknown statuses get the default;
any status containing "blocked" gets the blocked weight (config.py:51):

| Status | Weight |
|--------|:---:|
| To Do | 0.6 |
| In Progress | 0.9 |
| Code Review | 1.1 |
| In QA Review / QA Review | 1.3 |
| blocked (any) | 1.4 |
| default / unknown | 1.0 |

### 2.3 Size weight (`size_weight`, risk_components.py:105)
Keeps small tickets from being ignored, lets large tickets dominate a sprint's risk:

```
size_weight = clamp( 0.7 + (ticket_sp / avg_sprint_sp) * 0.3 )
clamp range: 0.4 .. 1.6
```

- ticket = avg sprint SP → weight **1.0**
- ticket = 2 SP, avg = 5 → `0.7 + 0.4*0.3 = 0.82`
- ticket = 5 SP, avg = 5 → `0.7 + 1.0*0.3 = 1.0`

### 2.4 Assignee factor (`assignee_factor`, risk_components.py:121)
A stalled ticket is more alarming when its assignee has no other work moving:

- **1.4** if the assignee is mid-sprint with no other active ticket updated within the staleness
  window (i.e. they look "idle").
- **1.0** if `Unassigned`/missing, or the assignee has other work progressing.

### 2.5 Trend factor (`trend_factor`, risk_components.py:188)
Applies to burndown only. Requires ≥ 2 historical burndown-gap check-ins; with insufficient history
it pessimistically assumes flat/widening so a fresh instance flags a behind sprint just as urgently
as a mature one:

| Trend of the gap (latest − previous) | Factor |
|--------------------------------------|:---:|
| shrinking fast (delta ≤ −max(prev×20%, 2)) | **0.7** |
| shrinking slowly | **1.0** |
| flat / widening / insufficient history | **1.3** |

### 2.6 Severity bands (`bucket_severity`, risk_components.py)

```
risk_score → severity
```

| risk_score | Severity |
|:---:|:---|
| ≥ 80 | **CRITICAL** |
| 60–79 | **HIGH** |
| 20–59 | **MEDIUM** |
| < 20 | **LOW** |

The frontend mirrors these exact bands in `frontend/src/utils/format.ts`:
`severityFromScore` (format.ts:26) and `getRiskColor` (format.ts:15) → CRITICAL `#ef4444`,
HIGH `#d97706`, MEDIUM `#f59e0b`, LOW `#10b981`. The matrix projection (§2.9) is built so that
`bucket_severity(project_matrix(P×I))` always equals the matrix band — the bands above therefore *are*
the ISO bands, with no second source of truth.

---

## 2.7 Common framework vs detector-specific logic

All nine detectors share one **scoring framework** — the 5×5 matrix — but each supplies its own
**base signal** expressed as an ordinal Probability × Impact pair. A detector never multiplies
anything: it derives `P` and `I` and lets the shared emitter (§2.8) project and bucket.

### 2.7.1 The common pipeline

```
detector telemetry → (P, I)  [1..5 each, via risk_matrix ladders]
        ─────────────────────────────────────────────────────────────
matrix_value = P × I                                    # 1..25
   │  project_matrix: piecewise-linear, band-aligned to 0..100
   ▼
risk_score (0–100 — drives the UI gauge + severity)
   │  matrix_severity(matrix_value)  ≡  bucket_severity(risk_score)
   ▼
LOW (1–4) · MEDIUM (5–9) · HIGH (10–14) · CRITICAL (15–25)
```

### 2.7.2 Which scoring model each detector uses

Every detector now uses the **matrix** (§2.9). The columns show the telemetry each detector's P and I
now read (the old product-model multiplier columns are gone).

| Detector / risk type | Model | Probability reads | Impact reads |
|---|:---:|---|---|
| SPRINT_NOT_STARTED | matrix | sprint elapsed | open ticket count |
| BURNDOWN_BEHIND | matrix | time left + gap trend | burndown gap % |
| QA_BOTTLENECK | matrix | clear ratio | queue size (+stuck) |
| SCOPE_CREEP | matrix | (creep happened → 5) | growth % (+adds/hikes) |
| SPRINT_ENDED_INCOMPLETE | matrix | (sprint ended → 5) | unfinished share |
| STORY_NOT_PROGRESSING | matrix | in-sprint silence hours | size (+blocking) |
| EXTERNAL_DEPENDENCY | matrix | dependency class | size (+blocking) |
| DUE_DATE_PASSED | matrix | days overdue | size |
| BUG_RAISED | matrix | open/fixed/escaped + age | defect tier |

### 2.7.3 Retired: the multiplier product model

The v2 engine scored each risk as `base × ∏(named multipliers)`, with per-detector bases, a
`RISK_RECIPES` table, and a `_apply_recipe` helper. That whole tail is **removed**: all nine
detectors now score through the shared matrix-only `_emit` (§2.9). The only surviving non-matrix
behavior is `SCOPE_CREEP`'s product rule, which floors the displayed score at **80 / CRITICAL** after
the projection so any confirmed creep stays red.

> `raw_score` and `risk_score` are still both emitted for ranking continuity: for every matrix risk
> `raw_score` is the continuous 0–100 projection (rounded to 1dp) and `risk_score` its rounded form, so
> the risks list sorted by `raw_score` stays comparable across detectors.

### 2.7.4 How the framework lives in the code

The per-detector (P, I) mapping in §2.7.2 lives in two places: the ladders are named functions in
`risk_matrix.py` (one per axis, one named table per detector — recalibrating a threshold is a one-line
edit), and the shared emitter `_emit` in `risk_engine.py` is the single place a risk's `risk_score`,
`raw_score`, and `severity` are computed. `validate_rubric.py` pins the numeric contract for every
detector, and `tests/test_risk_matrix.py` pins the projection/ladder invariants.

---

## 2.8 Implementation: the shared matrix emitter

Each detector now computes only its `(P, I)` pair (plus type-specific diagnostics) and calls the one
shared emitter, which owns projection, flooring, and severity:

```python
def _emit(self, risk_type, confidence, recommendation, probability, impact,
          score_floor=None, **extras):
    """Build the common risk payload for any detector from its (P, I) pair."""
    block = score_from_matrix(probability, impact)      # P×I -> matrix_value, project, band
    score, severity = block["risk_score"], block["severity"]
    if score_floor is not None:                          # SCOPE_CREEP product rule
        score = max(score, score_floor)
        severity = bucket_severity(score)
    risk = {"type": risk_type, "risk_score": score, "raw_score": round(block["raw_score"], 1),
            "confidence": confidence, "severity": severity, "recommendation": recommendation,
            "probability": block["probability"], "impact": block["impact"],
            "matrix_value": block["matrix_value"]}
    risk.update(extras)
    return risk
```

**Historical (retired): the v2 product model.** Before the matrix, every risk was
`base × ∏multipliers` with per-detector bases, a module-level `RISK_RECIPES` table and an
`_apply_recipe` helper, capped by `cap_score`; `BUG_RAISED` used a tier-band model and
`SPRINT_ENDED_INCOMPLETE` an additive formula. That entire tail — `RISK_RECIPES`, `_apply_recipe`,
`cap_score`'s role on the scoring path, and every legacy scoring constant in `config.py`
(`dependency_*_base`, `due_date_base_*`, `stalled_base_cap`, `bug_tier_bands`, `bug_p1_escaped_score`,
`blocking_factor`, `qa_backlog_cap`, `burndown_gap_cap`, `no_progress_*`) — was removed during the
matrix migration. Only the rubric assertions on `time_pressure_multiplier` and `scope_creep_cap` still
exercise the old helper/constants, so those two are retained.

---

## 2.9 The 5×5 Probability × Impact matrix (v3 scoring)

`backend/risk_matrix.py` implements a standard ISO 31005 style risk matrix. Instead of a
detector-specific base multiplied by ad-hoc factors, each risk derives two **1–5** ordinal scales from
Jira telemetry, and the score is their product:

```
probability P : 1..5   how likely is this risk to crystallise before the sprint ends?
impact      I : 1..5   how bad is it if it does?

matrix_value = P × I                       # 1..25
severity     = matrix band of (P × I)
risk_score   = project_matrix(P × I)       # band-aligned 0..100
```

**ISO 31005 bands over the 1–25 product** (`MATRIX_BANDS`):

| P × I | Severity |
|:---:|:---|
| 1–4 | **LOW** |
| 5–9 | **MEDIUM** |
| 10–14 | **HIGH** |
| 15–25 | **CRITICAL** |

**The projection trick.** The UI, gauge, RAG thresholds, aggregation and frontend `RISK_BANDS` all
require a 0–100 `risk_score`. `project_matrix` is a piecewise-linear map from 1–25 onto 0–100 whose
anchors land exactly on the legacy 0–100 severity boundaries:

| matrix_value | → risk_score | |
|:---:|:---:|:---|
| 1 | 0 | start LOW |
| 4 | 19 | end LOW |
| 5 | 20 | start MEDIUM |
| 9 | 59 | end MEDIUM |
| 10 | 60 | start HIGH |
| 14 | 79 | end HIGH |
| 15 | 80 | start CRITICAL |
| 25 | 100 | max CRITICAL |

Because the projection never crosses a band boundary, `bucket_severity(project_matrix(v))` **always
equals the matrix band**. This is the whole reason the UI needed no changes:
`validate_rubric.py` asserts it for all 25 products (via `assert_projection_is_band_aligned`).

**P and I are inferred, not human-assessed.** In classical risk practice a delivery lead rates P and I.
Here the model *derives* them from telemetry — an automated proxy for expert judgment, not a
replacement for it. Every ladder is an explicit ordered threshold table, so calibration is auditable
(`tests/test_risk_matrix.py`).

**Per-detector ladders — sprint-level.** Probability ladders ask "will it bite in time?"; impact
ladders ask "how much exposure if it does?":

| Detector | Probability P | Impact I |
|---|---|---|
| `SPRINT_NOT_STARTED` | sprint elapsed: ≤33%→3, ≤66%→4, else 5 | open tickets: ≤2→2, ≤5→3, ≤10→4, else 5 |
| `BURNDOWN_BEHIND` | `days_left/duration` (≥50%→2, ≥25%→3, ≥10%→4, else 5), ±1 by gap trend | gap%: ≤15→2, ≤30→3, ≤50→4, else 5 |
| `QA_BOTTLENECK` | `clear_days/days_left`: ≤0.5→1, ≤1→2, ≤1.5→3, ≤2.5→4, else 5 | queue size: ≤2→2, ≤4→3, ≤7→4, else 5; +1 if any story stuck >24h |
| `SCOPE_CREEP` | 5 (creep has already happened) | growth%: ≤10→2, ≤25→3, ≤50→4, else 5; ≥3 if work added/re-estimated |
| `SPRINT_ENDED_INCOMPLETE` | 5 (sprint already ended) | unfinished share of sprint: ≤10%→2, ≤25→3, ≤50→4, else 5; all-Done→1 |

**Per-detector ladders — ticket-level.** A shared size ladder drives impact (`≤1→2, ≤3→3, ≤5→4,
else 5`), bumped one step when the ticket blocks other work; the probability ladder encodes "will it
bite?":

| Detector | Probability P | Impact I |
|---|---|---|
| `STORY_NOT_PROGRESSING` | in-sprint silence `h`: ≥168h→5, ≥96h→4, ≥48h→3, else 2 | size (+1 if blocking) |
| `EXTERNAL_DEPENDENCY` | external→4, internal/unknown→3 | size (+1 if it blocks others) |
| `DUE_DATE_PASSED` | days overdue: ≥7→5, ≥3→4, ≥1→3 | size |
| `BUG_RAISED` | prod-escaped P1→5; open P1→3 (→4 past half-sprint age); fixed P1→2; other tiers→2 | tier: P1→5, P2→4, P3→3, P4→2 |

`STORY_NOT_PROGRESSING` P uses the **sprint-clamped** `h`, so pre-sprint silence never inflates it.
`DUE_DATE_PASSED` emits one aggregate risk carrying the **worst** ticket's P/I (max `matrix_value`).
`BUG_RAISED` impact is the defect tier, so a prod-escaped P1 is always `5 × 5 = 25 → CRITICAL`.

`SCOPE_CREEP` keeps its product rule ("any confirmed creep is a red flag") as a floor: after the matrix
projection, the displayed score is floored at **80 / CRITICAL** (`scope_creep_floor_score`), so even a
+1 SP addition is never silently green.

**Migration status: complete.** All nine detectors score through the shared matrix-only `_emit`, which
takes `(P, I)`, computes `P × I`, and applies the band-aligned projection. The old product model
(`RISK_RECIPES`, `_apply_recipe`, the per-detector bases and every legacy multiplier constant) has been
removed; `config.py` no longer carries the dead scoring knobs. `risk_explainer` renders the `P×I` chips
and matrix `severity_reason` for every risk.

---

## 3. Detector-by-detector formulas

### 3.1 STORY_NOT_PROGRESSING (ticket-level) — risk_engine.py:224
**Detector.** Flags tickets that went *quiet during the current sprint*. Staleness is measured from
`max(last update, sprint start)` so pre-sprint silence (backlog tickets pulled in) never inflates the
score — only in-sprint inactivity counts.

**Trigger.** Not Done AND `hours_since(max(updated, sprint_start)) > 24` (STALE_HOURS).

**Formula** (matrix, §2.9):

```
P = story_stalled_p(h)                      # in-sprint silence: ≥168h→5, ≥96h→4, ≥48h→3, else 2
I = story_stalled_i(sp, blocks_others)      # size ladder, +1 if it blocks other work

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

`confidence: 85`. Worked examples (validate_rubric.py:419):
- Ticket stale since before a 5-day sprint that started 30h ago → clamped to ~30h silence (`P=2`),
  fires.
- Mid-sprint ticket updated 10h ago → stays silent (no risk).
- The same stale ticket in a week-old sprint (more in-sprint silence) scores strictly higher.

### 3.2 SPRINT_NOT_STARTED (sprint-level) — risk_engine.py:228
**Detector.** A sprint that is *currently active* but where 0 tickets have moved out of a start column.
It exists because STORY_NOT_PROGRESSING only judges per-ticket silence and is gated by the burndown
grace period; this judges the whole sprint's progress state.

**Trigger.** Sprint active AND `days_elapsed ≥ no_progress_grace_days` (default **2**) AND at least
one open ticket AND every open ticket is in a start column
(`To Do / todo / backlog / open / selected for development`).

**Formula** (matrix, §2.9):

```
P = sprint_not_started_p(days_elapsed / duration)  # ≤33%→3, ≤66%→4, else 5
I = sprint_not_started_i(open_count)              # ≤2→2, ≤5→3, ≤10→4, else 5

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

`confidence: 90`. Worked examples (validate_rubric.py:440):
- 3 days into a 14-day sprint (elapsed 21%), 3 tickets all To Do: `P=3`, `I=3`,
  `matrix = 9` → **MEDIUM (59)**.
- One ticket In Progress → stays silent.
- Day 1 (inside grace window) or pre-start sprints → stay silent.

### 3.3 BURNDOWN_BEHIND (sprint-level) — risk_engine.py:361
**Detector.** Compares actual completion (weighted by `status_completion_weights`) against the ideal
linear burn line. Not judged before the sprint is 25% elapsed (grace period).

**Metrics:**

```
expected_completion_rate = min(1.0, days_elapsed / duration)
actual_completion_rate   = weighted_completed_sp / total_sp
burndown_gap_percent     = (expected - actual) * 100
```

**Trigger.** `burndown_gap > burndown_behind_threshold` (default **10%**).

**Formula** (matrix, §2.9):

```
P = burndown_p(days_left / duration, trend_factor(history))
    # time left (≥50%→2, ≥25%→3, ≥10%→4, else 5), nudged ±1 by the gap trend
I = burndown_i(burndown_gap_percent)       # ≤15→2, ≤30→3, ≤50→4, else 5

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

`confidence: 90`. Worked examples (validate_rubric.py:76):
- Gap 100%, day 4/4 (no time left), widening trend: `P=5`, `I=5`, `matrix = 25` → **100 CRITICAL**.
- Same issues, day 1/4 (75% of sprint left): `P=3` → `matrix = 15` → **80 CRITICAL**, strictly
  lower than the day-4 score (urgency still works).

The per-check-in gap is persisted (`main.py:238`) as a capped 8-point history so the trend factor
can see widening vs shrinking.

### 3.4 QA_BOTTLENECK (sprint-level) — risk_engine.py:405
**Detector.** Models QA as a capacity-vs-time constraint: can the QA queue clear before the sprint ends?

**Trigger.** At least `qa_bottleneck_threshold` (**2**) stories in a QA column (accepts both
`QA Review` and `In QA Review` naming).

**Metrics:**
- `throughput` = historical cleared tickets/day over a rolling 3-sprint window
  (`qa_throughput_per_day`, risk_components.py:172), falling back to `1.0`/day with no history.
- `backlog_clear_days = qa_queue_count / throughput`
- `days_left = days_remaining(sprint)` — inline comment notes "calendar" convention `duration − elapsed`.

**Formula** (matrix, §2.9):

```
P = qa_p(backlog_clear_days / days_left)     # ≤0.5→1, ≤1→2, ≤1.5→3, ≤2.5→4, else 5
I = qa_i(qa_queue_count, stuck_count)         # ≤2→2, ≤4→3, ≤7→4, else 5; +1 if any stuck

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

Note `P` is the *clear ratio*: a queue that clears comfortably **before** the sprint ends is
`Possible` or lower, whereas the legacy `(clear/days_left) × 100` base treated a comfortable ratio
(such as 2/3) as ~67 raw — over-rating a healthy QA column.

`confidence: 80`. Worked examples (validate_rubric.py:215):
- 2 in QA (both >24h stale), throughput 1/day → clear in 2d; 3 days left: `P=2`, `I=2+1(stuck)=3`,
  `matrix = 6` → **30 MEDIUM**.
- Same queue, 1 day left: `clear_ratio = 2/1` → `P=4`, `I=3`, `matrix = 12` → **70 HIGH**.

### 3.5 EXTERNAL_DEPENDENCY (ticket-level) — risk_engine.py:475
**Detector.** Keyword scan of the free-text description for dependency language. Not Done required.
The keyword class is retained as a probability input:
- **external** (`vendor`, `third-party`, `procurement`, `external`, `credentials`) → `P=4` (Likely;
  least controllable)
- **internal** (`another team`, `internal`, `platform team`, `another squad`, `squad`) → `P=3`
- **default** (either/both keywords) → `P=3`

**Formula** (matrix, §2.9):

```
P = external_dep_p(kind)                    # external→4, internal/default→3
I = external_dep_i(sp, blocks_others)       # size ladder, +1 if it blocks other tickets

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

`confidence: 75`. Worked examples (validate_rubric.py:254):
- External vendor, blocks 2 tickets, 5 SP: `P=4`, `I=4+1(blocking)=5`, `matrix = 20` → **90 CRITICAL**.
- Internal "Platform team" dependency, nothing blocked, 2 SP: `P=3`, `I=3`, `matrix = 9` → **59 MEDIUM**.

### 3.6 DUE_DATE_PASSED (sprint-level, max-of-tickets) — risk_engine.py:519
**Detector.** Collects tickets with a due date strictly before *today's calendar date in the user's
Jira timezone* (so "overdue" matches what the user sees in Jira even if the server runs in UTC).

**Per-ticket formula** (matrix, §2.9):

```
P = due_date_p(days_overdue)               # ≥7→5, ≥3→4, ≥1→3
I = due_date_i(sp)                          # size ladder only (lateness is captured by P)

matrix_value = P × I
```

**Aggregation:** the single sprint-level risk carries the **worst** ticket's P/I (the max
`matrix_value` over all overdue tickets), and each ticket in `overdue_issues` keeps its own P/I and
projected score for the detail view.

`confidence: 85`. Worked examples (validate_rubric.py:112):
- PFIN-10, 1 day overdue, 5 SP: `P=3`, `I=4`, `matrix = 12` → **70 HIGH**.
- 4 days overdue, 5 SP: `P=4`, `I=4`, `matrix = 16` → **82 CRITICAL**.

### 3.7 BUG_RAISED (ticket-level, in-sprint defect) — risk_engine.py:586
**Detector.** Flags bugs **created during the current sprint** (created ≥ sprint start) — defects this
sprint *injected*, not backlog debt pulled in. Jira priority maps to a quality-risk tier:

| Priority | Tier | Default |
|----------|:---:|:---:|
| Highest | P1 | |
| High | P2 | |
| Medium | P3 | |
| Low / Lowest | P4 | |
| unknown/missing | — | **P3** |

**Matrix model** (risk_matrix.py `bug_i` / `bug_p`) — the tier sets **impact**, and whether the defect
is open, fixed, or a production escape sets **probability**:

```
I = bug_i(tier)                            # P1→5, P2→4, P3→3, P4→2
P = bug_p(tier, done, age_fraction, escaped)
    escaped P1             → 5
    open P1, age ≤ 50%     → 3   (age > 50% → 4: a lingering critical defect worsens)
    fixed P1               → 2   (contained; verify fix / regression)
    any other tier, open   → 2

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

A prod-escaped P1 (labeled `production` / `prod-escape`) is always `5 × 5 = 25 → 100 CRITICAL`.

**Filters:**
- Fixed `P2+` defects are dropped (normal quality variation); fixed P1s stay visible lower down.
- A P1 fixed but past the sprint end is also dropped (defect contained — no active risk).

`confidence: 80`. Worked examples (validate_rubric.py:133):
- P2 (High) open, 4/10 days old: `P=2`, `I=4`, `matrix = 8` → **49 MEDIUM**.
- P4 open, fresh: `P=2`, `I=2`, `matrix = 4` → **19 LOW**.
- P1 open, fresh: `P=3`, `I=5`, `matrix = 15` → **80 CRITICAL**.
- P1 fixed, 4d old: `P=2`, `I=5`, `matrix = 10` → **60 HIGH**.
- Prod-escaped P1: `5 × 5 = 25` → **100 CRITICAL**.
- Bug created *before* sprint start → not flagged; fixed P2 → skipped.

### 3.8 SCOPE_CREEP (sprint-level, vs first-active-sync baseline) — risk_engine.py:672
**Detector.** Compares against a baseline captured automatically on the **first sync while the sprint
is active** (the planning commitment). Requires ≥ 2 scope-history points before judging, so
single-sync noise and future sprints never fire.

**Triggers** (any of):
- Net SP growth ≥ `scope_creep_min_growth_pct` (**10%**) vs baseline,
- any issue **added** after baseline,
- any **estimate hike** (baseline SP raised).

**Formula** (matrix, §2.9):

```
growth = (current_sp - baseline_sp) / baseline_sp * 100

P = 5                                                # creep has already happened
I = scope_creep_i(growth, added_or_hiked)             # ≤10%→2, ≤25%→3, ≤50%→4, else 5; ≥3 if added/hiked

matrix_value = P × I
risk_score   = max(project_matrix(matrix_value), scope_creep_floor_score)   # floor 80 → ALWAYS CRITICAL
```

**Product rule:** *any* confirmed scope creep is a red flag, even +1 SP — after the matrix projection
the displayed score is still floored at **80 / CRITICAL** (`scope_creep_floor_score`, config.py), while
`raw_score` (the matrix projection) is still serialized for triage. `confidence: 75` (or **60** if the
baseline was captured late, i.e. > 24h after sprint start).

Worked examples (validate_rubric.py:470):
- Baseline 3 SP, re-estimated to 5 SP (`growth = 66.7%`): `P=5`, `I=5`, `matrix = 25` → **100 CRITICAL**
  (the floor is not even needed at this size).
- No growth / no hikes / no additions → silent.
- Only 1 history point → silent (needs ≥ 2).
- A single +5 SP added issue is still CRITICAL.

### 3.9 SPRINT_ENDED_INCOMPLETE (sprint-level) — risk_engine.py:768
**Detector.** Fires when the sprint's end date has passed **but Jira still lists it as active**.
Catches the "no risk shown" gap where every in-flight detector has already gone quiet.

**Trigger.** `now > endDate` (calendar days in the Jira timezone).

**Formula** (matrix, §2.9):

```
P = 5                                                # the sprint already ended
I = sprint_ended_i(remaining_sp, total_sp)           # unfinished share: ≤10%→2, ≤25%→3, ≤50%→4, else 5
                                                    # all work Done → 1 (housekeeping only)

matrix_value = P × I
risk_score   = project_matrix(matrix_value)
```

`confidence: 90`. Worked examples (validate_rubric.py:280):
- Ended 1 day ago, 8 of 16 SP remain (half the sprint): `P=5`, `I=4`, `matrix = 20` → **90 CRITICAL**.
- Ended 1 day ago but all Done: `I=1`, `matrix = 5` → **20 MEDIUM** (still flagged — cleanup risk).
- Sprint still in flight → silent.
- Timezone lock: PFIN (18:15Z) and MOS (12:45Z) boundaries resolve to the same Jira calendar day
  under `Asia/Kathmandu` and therefore the same `days_overdue`.

---

## 4. Next-sprint (pre-planning) risks — v1 formulas, out of v2 scope

`calculate_next_sprint_risks` (risk_engine.py:835) scores upcoming-sprint hygiene with simple
linear formulas (raw v1 style, `severity` set directly rather than bucket):

| Type | Formula | Severity |
|------|---------|:---:|
| `UNASSIGNED` | `min(100, 40 + count×10)` | HIGH if count ≥ 3 else MEDIUM |
| `UNESTIMATED` | `min(100, 35 + count×8)` | MEDIUM |
| `UNDEFINED_SCOPE` | `min(100, 30 + count×8)` | MEDIUM |
| `SIZING_RISK` | `min(100, 50 + count×15)` (issues ≥ 8 SP) | MEDIUM |

It also reuses `EXTERNAL_DEPENDENCY` and `DUE_DATE_PASSED` rules. Project readiness =
`aggregate_risk_score` (risk_engine.py:911):

```
score = top score + Σ(remaining scores × 0.2),   capped 100
```

---

## 5. How scores surface in the UI

1. **Severity is recomputed client-side, not trusted.** Every component derives severity from
   `risk_score` via `severityFromScore` (format.ts:26) and falls back to the backend's `severity`
   field only when the score is absent — bands are mirrored to stay in sync.
2. **Sprint radar card** (`snapshot.py:_build_radar_data` → `RiskRadar` → `SprintCard`).
   `radar_data` collapses each sprint to its **worst** risk (`risk_sort_key`: severity rank, then
   score, snapshot.py:70), and the card's gauge shows that risk's `risk_score`. The full risk-type
   list and severity counts per sprint are also surfaced so the worst-case aggregation stays
   transparent. A sprint with no risks renders as `ON_TRACK` with score 0.
3. **SprintGauge** (`SprintGauge.tsx`): a circular SVG gauge colored by `getRiskColor(score)`
   showing `score% risk`. Blue arc → LOW (green), amber → MEDIUM, deepened amber → HIGH, red → CRITICAL.
4. **Risk card** (`RiskCardItem.tsx`): severity header (`critical/high/medium/low` + category label),
   and the raw score shown next to the Risk Signals header as `(Score: N)` (RiskCardItem.tsx:142).
   The card renders the explainer's three-tier transparency view — Risk Signals (+facts),
   Suspected cause, Suggested actions — plus the human-decision chip. `SprintDetailPanel`
   sorts blockers by severity rank then score.
5. **Backend transparency layer** (`risk_explainer.py`): every risk gets
   - a deterministic, sync-stable `risk_id` (type + sprint + anchored issue key),
   - a `severity_reason` sentence naming the two scales and the band, e.g.
     "Why MEDIUM? … → P2 Unlikely × I3 Moderate = 6 → MEDIUM (20-59)".
   - a `factors` block with score-math chips: the `P×I` band chip + the named scale.
   These render as per-risk chips on the risk card via `scoreDrivers` (format.ts).
6. **Health rollups:**
   - `generate_risk_summary` (risk_engine.py:920):
     `overall_sprint_health = max(0, 100 − (high×20 + medium×10))`.
   - `DeliveryHealth` (snapshot.py:221): RAG = RED when a CRITICAL/HIGH risk exists or burndown
     gap ≥ 25; AMBER for medium/other risks or gap ≥ 10; GREEN otherwise. `timeline_risk_score`
     is a forecast-style score (60 baseline when no forecast, 55 + delay_days×15 when late, 20 on
     track), then bumped to min 45 (AMBER) / min 75 (RED) and blended + `gap×0.5`.

---

## 6. Verification

`backend/validate_rubric.py` reconstructs every worked example above as synthetic sprint/ticket data
and asserts the produced `risk_score`/severity with tolerance:

```
cd backend && python3 validate_rubric.py
```

It additionally locks: detector isolation (one injected failure doesn't abort the rest), prompt
privacy pseudonymization, timezone-aware overdue math, sprint-clamped stale-hour math, and
scope-baseline persistence. It also hard-asserts the **matrix band-alignment invariant**
(`assert_projection_is_band_aligned`: every 1–25 product projects into its own severity band), which is
what guarantees the UI/RAG bands never disagree with the matrix. Per the repo's convention, backend
changes must keep this passing.

Unit tests for the matrix and explainer live in `tests/test_risk_matrix.py` and
`tests/test_risk_explainer.py` (pytest style; the projection anchors, monotonicity, band mapping, and
P/I factor chips are all covered). Current status **67/67 PASS** (pydantic lives in the venv, so run
with `./venv/bin/python validate_rubric.py`).

---

## 7. Review observations (found while tracing)

- `risk_explainer.py` emits `factors` / `severity_reason` with `P×I` content for every risk. The
  per-risk score-math chips **are** rendered on the risk card via `scoreDrivers` (format.ts).
- The 5×5 matrix is now the **single scoring model for all nine detectors**; the product/band models
  are retired. `severity_reason` is uniformly the `P×I` form.
- `delivery_health` (RAG / `timeline_risk_score`) and `summary.overall_sprint_health` are populated
  in the snapshot but have **no UI component** on the current dashboard (`DashboardHome` renders
  RiskRadar, NextSprintOverview, VelocityTrend only).
- `RISK_TYPE_META` still maps `STALLED_TICKETS` (`🕒 Stalled Tickets`) in `frontend/src/utils/format.ts:58`,
  but the engine no longer emits that type (it was folded into `STORY_NOT_PROGRESSING`).
- The matrix UI (`RiskDetailMatrix`) is a **reference** grid: it uses the same ISO bands as the model
  but does not yet highlight the live risks' cells. Highlighting the P/I of active risks on the grid
  is a natural follow-up.
- These are **display gaps, not scoring bugs** — do not modify the rubric math for them.