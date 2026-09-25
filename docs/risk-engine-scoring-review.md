# Sprint Risk Engine — Scoring & Severity Review

A deep-dive into how the v2 risk engine (`backend/risk_engine.py` and `backend/risk_components.py`)
detects sprint risks, computes each score, and maps it to severity. Every rule is pinned by the
worked examples in `backend/validate_rubric.py` (`python3 validate_rubric.py`).

> **Key mental model:** each risk emits two numbers —
> `risk_score` (0–100, **capped**, drives the UI gauge and severity) and
> `raw_score` (uncapped, used for sprint-to-sprint and ticket-to-ticket triage). All detectors in v2
> keep the same *triggers* as v1; only the scoring formulas changed.

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

Risks come back sorted by `raw_score` descending, so the worst (uncapped) driver is always first.
The resolved list is enriched by `risk_explainer.py` (signal → suspected cause → suggested action,
plus a `severity_reason` sentence and a stable `risk_id`) and assembled into the snapshot by
`snapshot.py`.

---

## 2. Shared scoring components

Every detector leans on the same table-driven helpers in `risk_components.py`, all tunable from
`config.py`. These are the building blocks whose products appear throughout §3.

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

### 2.6 Score capping & severity (`cap_score` / `bucket_severity`, risk_components.py:156)
```
risk_score = min(100, round(raw_score))
```

| risk_score | Severity |
|:---:|:---|
| ≥ 80 | **CRITICAL** |
| 60–79 | **HIGH** |
| 20–59 | **MEDIUM** |
| < 20 | **LOW** |

The frontend mirrors these exact bands in `frontend/src/utils/format.ts`:
`severityFromScore` (format.ts:26) and `getRiskColor` (format.ts:15) → CRITICAL `#ef4444`,
HIGH `#d97706`, MEDIUM `#f59e0b`, LOW `#10b981`.

---

## 2.7 Common framework vs detector-specific logic

All nine detectors share one **scoring framework**, but each supplies its own **base signal** and a
different **mix of the shared multiplier tables** from §2. Two detectors deliberately break out of
the `base × multiplier` pattern entirely.

### 2.7.1 The common pipeline

```
detector-specific base signal (severity driver)
        ×  the detector's chosen shared multipliers (time-pressure, stage, size, trend, …)
        ─────────────────────────────────────────────────────────────
raw_score  (uncapped — used for cross-sprint / cross-ticket triage, keeps the ranking honest)
   │  cap_score: min(100, round(...))
   ▼
risk_score (capped 0–100 — drives the UI gauge + severity)
   │  bucket_severity
   ▼
LOW (<20) · MEDIUM (20–59) · HIGH (60–79) · CRITICAL (80+)
```

### 2.7.2 Which multiplier each detector uses

| Detector / risk type | Base signal (severity driver) | Time-pressure | Stage | Size | Trend | Assignee | Fan-out / blocking |
|---|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| STORY_NOT_PROGRESSING | `min(50, hours/2)` | | ● | ● | | ● | |
| SPRINT_NOT_STARTED | `min(60, days×12)` | ● | | | | | |
| BURNDOWN_BEHIND | `min(60, gap%)` | ● | | | ● | | |
| QA_BOTTLENECK | `min(70, (clear_days/days_left)×100)` | ● | | | | | |
| EXTERNAL_DEPENDENCY | constant `75/50/50` by keyword class | | | ● | | | ● (fan-out 1.3) |
| DUE_DATE_PASSED | `min(70, days_overdue×15)` | | ● | ● | | | ● (blocking 1.3) |
| BUG_RAISED | priority→tier band, age-interpolated | | | | | | |
| SCOPE_CREEP | `min(85, growth%)` | ● | | | | | |
| SPRINT_ENDED_INCOMPLETE | additive `60 + days×4 + remaining×1.5` | | | | | | |

### 2.7.3 The exceptions to the multiplier pattern

- **BUG_RAISED** (risk_engine.py:569) — a **band model**, not `base × multiplier`: the Jira
  priority maps to a tier, each tier owns a score range (`P1_open` 80–90, `P1_fixed` 60–70,
  `P2` 30–50, `P3`/`P4` 10–20), and the score is **linearly interpolated inside the band by defect
  age across the sprint**. A prod-escaped P1 short-circuits to 100. No shared multiplier is applied
  (`RISK_RECIPES["BUG_RAISED"] = ()`).
- **SPRINT_ENDED_INCOMPLETE** (risk_engine.py:768) — a purely **additive linear formula**
  (`60 + days_overdue×4 + min(remaining_sp, 20)×1.5`, or `40 + days_overdue×3` when all work is Done),
  with no multiplier tables (`RISK_RECIPES["SPRINT_ENDED_INCOMPLETE"] = ()`).
- **SCOPE_CREEP** — uses the multiplier path (`min(85, growth%) × time-pressure`) but is a *hybrid*:
  the product rule ("any confirmed creep is a red flag") means the displayed score is floored at
  **80 / CRITICAL** regardless of how small the growth was.

> This is why the engine emits both `raw_score` and `risk_score`: the multiplier-heavy rules can
> produce very different raw magnitudes, and capping them all at 100 for display would make the truly
> dominant driver indistinguishable in sprint-to-sprint triage — the uncapped `raw_score` preserves
> that ordering (the risks list is sorted by it, risk_engine.py:131).

### 2.7.4 How the framework lives in the code

The multiplier matrix in §2.7.2 is now a first-class artifact in `risk_engine.py` — the module-level
`RISK_RECIPES` table (risk_engine.py:70) maps every risk type to the ordered list of multiplier
**names** that apply to it, and the shared pipeline (§2.8) consumes it. Updating "which multiplier a
detector uses" is now a one-line table edit instead of editing a detector's math.

---

## 2.8 Implementation: original vs refactored (behavior-preserving)

The scoring math in §3 is the contract and is **unchanged**. The original implementation inlined each
detector's risk-dict tail (the same ~5 keys `risk_score` / `raw_score` / `confidence` / `severity` /
`recommendation`) and multiplied each factor inline. A refactor collapsed that into one shared
pipeline — `RISK_RECIPES` + `_apply_recipe` + `_emit` — with **no numeric change**. This section keeps
both versions so the mapping stays auditable.

### Original (pre-refactor) — inline product + per-detector dict tail

Every multiplier-capable detector spelled out its full formula and then pasted a near-identical tail:

```python
# STORY_NOT_PROGRESSING — original
base = min(settings.stalled_base_cap, h / 2.0)
stage = workflow_stage_weight(issue.get("status"))
af = assignee_factor(issues, issue.get("assignee"))
sw = size_weight(issue.get("story_points") or 0, avg_sp)
raw = base * stage * af * sw
score = cap_score(raw)
risks.append({
    "type": "STORY_NOT_PROGRESSING",
    "issue_key": key, "summary": ..., "assignee": ..., "status": ..., "hours_since_update": ...,
    "risk_score": score,
    "raw_score": round(raw, 1),
    "confidence": 85,
    "severity": bucket_severity(score),
    "recommendation": (...),
    **detail,
})
```

The 9 detectors duplicated this tail ~9–14 times (cap_score ×10, "risk_score" ×14, severity ×9).

### Refactored — recipe-driven base signal + one shared emitter

Now a detector only supplies its **base**, the **factors it actually computed**, and its **extras**;
the recipe decides the multipliers and the tail is emitted once:

```python
# STORY_NOT_PROGRESSING — refactored
base = min(settings.stalled_base_cap, h / 2.0)
factors = {
    "stage": workflow_stage_weight(issue.get("status")),
    "assignee": assignee_factor(issues, issue.get("assignee")),
    "size": size_weight(issue.get("story_points") or 0, avg_sp),
}
raw = self._apply_recipe(base, RISK_RECIPES["STORY_NOT_PROGRESSING"], factors)
risks.append(self._emit("STORY_NOT_PROGRESSING", raw, 85, recommendation,
                        issue_key=key, ..., **detail))
```

**The two helpers** (risk_engine.py:142 / risk_engine.py:156):

```python
def _apply_recipe(base, recipe, values):
    """Multiply base by each named multiplier in `recipe`; missing factors ~ 1.0."""
    for name in recipe:
        base *= values.get(name, 1.0) or 1.0
    return base

def _emit(risk_type, raw, confidence, recommendation, score_floor=None, **extras):
    """Single scoring tail: risk_score = min(100, round(raw)); bucket severity."""
    score = max(cap_score(raw), score_floor) if score_floor is not None else cap_score(raw)
    risk = {"type": ..., "risk_score": score, "raw_score": round(raw, 1),
            "confidence": ..., "severity": bucket_severity(score), "recommendation": ...}
    risk.update(extras)
    return risk
```

**Why this preserves every number exactly:**

- `cap_score` itself rounds (`risk_components.py:167` → `min(100, int(round(raw)))`), so the original
  BUG_RAISED `cap_score(int(round(raw)))` is identical to `_emit`'s `cap_score(raw)` — every band
  boundary lands the same. All other detectors already called `cap_score(raw)` directly.
- `raw_score = round(raw, 1)` was identical across every original tail.
- Empty recipes (`BUG_RAISED`, `SPRINT_ENDED_INCOMPLETE`) multiply nothing, so the band and additive
  models pass through untouched.
- `SCOPE_CREEP` dropped its explicit `score = max(cap_score(raw), floor)` into `_emit`'s
  `score_floor` argument — same CRITICAL floor, same `raw_score`.

**Per-risk mapping (original → refactored):**

| Risk type | `RISK_RECIPES` | Factors fed to `_apply_recipe` | Extras in `_emit` (payload byte-identical) |
|---|:---:|---|---|
| STORY_NOT_PROGRESSING | `(stage, assignee, size)` | stage, assignee, size | issue_key, summary, assignee, status, hours_since_update, stage_weight, assignee_factor, size_weight |
| SPRINT_NOT_STARTED | `(pressure,)` | pressure | sprint_key, summary, days_elapsed, open_count |
| BURNDOWN_BEHIND | `(trend, pressure)` | trend, pressure | sprint_key, issue_keys, total_sp, completed_sp, weighted_completed_sp, remaining_sp, days_remaining, burndown_gap_percent |
| QA_BOTTLENECK | `(pressure,)` | pressure | sprint_key, issue_keys, qa_stories_count, qa_throughput_per_day, backlog_clear_days, days_remaining, stuck_stories |
| EXTERNAL_DEPENDENCY | `(fan_out, size)` | fan_out, size | issue_key, summary, dependency_detail, dependency_base, fan_out |
| DUE_DATE_PASSED | `(stage, size, blocking)` | per-ticket stage, size, blocking | sprint_key, issue_keys, overdue_issues, count |
| BUG_RAISED | `()` band model | — | sprint_key, issue_key, issue_keys, summary, assignee, status, priority, tier, band, days_since_created |
| SCOPE_CREEP | `(pressure,)` | pressure (+ `score_floor`) | sprint_key, issue_keys, baseline_sp, current_sp, growth_percent, added_issues, story_point_hikes, late_baseline |
| SPRINT_ENDED_INCOMPLETE | `()` additive | — | sprint_key, issue_keys, total_sp, completed_sp, remaining_sp, days_overdue |

**Dead code removed in the refactor:** `config.py`'s `stalled_base_per_2h = 1.0` (was line 75) was
never read — `_stalled_ticket_score` always hardcoded `h / 2.0`. It is gone; no behavior change.

---

## 3. Detector-by-detector formulas

### 3.1 STORY_NOT_PROGRESSING (ticket-level) — risk_engine.py:182
**Detector.** Flags tickets that went *quiet during the current sprint*. Staleness is measured from
`max(last update, sprint start)` so pre-sprint silence (backlog tickets pulled in) never inflates the
score — only in-sprint inactivity counts.

**Trigger.** Not Done AND `hours_since(max(updated, sprint_start)) > 24` (STALE_HOURS).

**Formula** (`_stalled_ticket_score`, risk_engine.py:283):

```
base  = min(50, hours_silent / 2)          # 1 idle-hour → +0.5, capped 50
stage = workflow_stage_weight(status)      # §2.2
af    = assignee_factor(issues, assignee)  # §2.4 (1.4 / 1.0)
sw    = size_weight(sp, avg_sp)            # §2.3

raw_score = base * stage * af * sw
```

`confidence: 85`. Worked examples (validate_rubric.py:412):
- Ticket stale since before a 5-day sprint that started 30h ago → clamped to ~30h silence, fires.
- Mid-sprint ticket updated 10h ago → stays silent (no risk).
- The same stale ticket in a week-old sprint (more in-sprint silence) scores strictly higher.

### 3.2 SPRINT_NOT_STARTED (sprint-level) — risk_engine.py:228
**Detector.** A sprint that is *currently active* but where 0 tickets have moved out of a start column.
It exists because STORY_NOT_PROGRESSING only judges per-ticket silence and is gated by the burndown
grace period; this judges the whole sprint's progress state.

**Trigger.** Sprint active AND `days_elapsed ≥ no_progress_grace_days` (default **2**) AND at least
one open ticket AND every open ticket is in a start column
(`To Do / todo / backlog / open / selected for development`).

**Formula:**

```
base = min(60, days_elapsed * 12)          # 12/day, capped 60
tp   = time_pressure_multiplier(sprint)    # §2.1

raw_score = base * tp
```

`confidence: 90`. Worked examples (validate_rubric.py:440):
- 3 days elapsed, all tickets To Do → fires (severity MEDIUM, score ≥ 20).
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

**Formula:**

```
base = min(60, burndown_gap_percent)       # gap cap 60
tf   = trend_factor(burndown_history)      # §2.5 (0.7 / 1.0 / 1.3)
tp   = time_pressure_multiplier(sprint)    # §2.1

raw_score = base * tf * tp
```

`confidence: 90`. Worked examples (validate_rubric.py:76):
- Gap 100 (capped 60), day 4/4 (pressure 1.7), flat trend 1.3 → `60 × 1.3 × 1.7 = 132` → **100 CRITICAL**.
- Same issues, day 1/4 (pressure 0.6) → strictly lower than the day-4 score (time pressure works).

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

**Formula:**

```
base = min(70, (backlog_clear_days / days_left) * 100)   # % of remaining time needed to clear
tp   = time_pressure_multiplier(sprint)

raw_score = base * tp
```

`confidence: 80`. Worked examples (validate_rubric.py:215):
- 2 in QA, throughput 1/day → backlog 2; 3 days left (day 2/5, pressure 0.8):
  `base = min(70, (2/3)·100) = 66.7`, × 0.8 = **53.3 → 53 MEDIUM** (the validator asserts 54,
  which it accepts under its ±2 tolerance).
- Same queue, 1 day left (pressure 1.7): `70 × 1.7 = 119` → **100 HIGH**.

### 3.5 EXTERNAL_DEPENDENCY (ticket-level) — risk_engine.py:457
**Detector.** Keyword scan of the free-text description for dependency language. Not Done required.

**Base score by dependency class** (risk_engine.py:51, config.py:70):
- **external** (`vendor`, `third-party`, `procurement`, `external`, `credentials`) → base **75**
- **internal** (`another team`, `internal`, `platform team`, `another squad`, `squad`) → base **50**
- **default** (either/both keywords) → base **50**

**Formula:**

```
fan_out = 1.3 if this ticket blocks other tickets (blocking_map) else 1.0
sw      = size_weight(sp, avg_sp)           # §2.3

raw_score = dependency_base * fan_out * sw
```

`confidence: 75`. Worked examples (validate_rubric.py:248):
- External vendor, blocks 2 tickets (fan-out 1.3), 5 SP (size 1.0) →
  `75 × 1.3 × 1.0 = 97.5` → **98 CRITICAL** (validator accepts 97 under tolerance).
- Internal "Platform team" dependency, nothing blocked, 2 SP vs avg 5 (size 0.82) →
  `50 × 1.0 × 0.82 = 41` → **41 MEDIUM**.

### 3.6 DUE_DATE_PASSED (sprint-level, max-of-tickets) — risk_engine.py:502
**Detector.** Collects tickets with a due date strictly before *today's calendar date in the user's
Jira timezone* (so "overdue" matches what the user sees in Jira even if the server runs in UTC).

**Per-ticket formula:**

```
base     = min(70, days_overdue * 15)       # 15/day, capped 70
stage    = workflow_stage_weight(status)    # §2.2
sw       = size_weight(sp, avg_sp)          # §2.3
blocking = 1.3 if this ticket blocks others else 1.0

ticket_raw = base * stage * sw * blocking
```

**Aggregation:** the sprint-level risk takes the **maximum** per-ticket raw score, then caps:

```
raw_score  = max(ticket_raw over all overdue tickets)
risk_score = min(100, round(raw_score))
```

`confidence: 85`. Worked examples (validate_rubric.py:112):
- PFIN-10, 1 day overdue, Code Review (1.1), 5 SP (size 1.0), blocks 1 (1.3):
  `min(70,15) × 1.1 × 1.0 × 1.3 = 15 × 1.43 = 21.45` → **21** (LOW–MEDIUM boundary).
- 4 days overdue, QA (1.3), blocks 2 (1.3): `min(70,60) × 1.3 × 1.0 × 1.3 = 101.4` → **100 HIGH**.

### 3.7 BUG_RAISED (ticket-level, in-sprint defect) — risk_engine.py:569
**Detector.** Flags bugs **created during the current sprint** (created ≥ sprint start) — defects this
sprint *injected*, not backlog debt pulled in. Jira priority maps to a quality-risk tier:

| Priority | Tier | Default |
|----------|:---:|:---:|
| Highest | P1 | |
| High | P2 | |
| Medium | P3 | |
| Low / Lowest | P4 | |
| unknown/missing | — | **P3** |

**Band model** (config.py:88) — each tier maps to a score band; position within the band ramps
linearly with defect age across the sprint duration:

```
frac = min(1.0, days_old / sprint_days)          # age fraction of the sprint
raw  = low + (high - low) * frac
```

| Band | Range | Notes |
|------|:---:|------|
| `P1_open` | 80–90 | open P1 defect |
| `P1_fixed` | 60–70 | P1 fixed before sprint end — stays visible for regression risk |
| `P2` | 30–50 | |
| `P3` / `P4` | 10–20 | |
| `P1_escaped` | **100** | P1 labeled `production` / `prod-escape` — scores max regardless of status/age |

**Filters:**
- Fixed `P2+` defects are dropped (normal quality variation); fixed P1s stay visible lower down.
- A P1 fixed but past the sprint end is also dropped (defect contained — no active risk).

`confidence: 80`. Worked examples (validate_rubric.py:133):
- P2 (High), 4/10 days old, sprint 10d: `30 + (50−30)×0.4 = 38` → **38 MEDIUM**.
- P4 fresh today: **10 LOW**.
- P1 open fresh: **80 CRITICAL**. P1 fixed, 4d old: `60 + 10×0.4 = 64` **HIGH**.
- Prod-escaped P1: **100 CRITICAL**.
- Bug created *before* sprint start → not flagged; fixed P2 → skipped.

### 3.8 SCOPE_CREEP (sprint-level, vs first-active-sync baseline) — risk_engine.py:672
**Detector.** Compares against a baseline captured automatically on the **first sync while the sprint
is active** (the planning commitment). Requires ≥ 2 scope-history points before judging, so
single-sync noise and future sprints never fire.

**Triggers** (any of):
- Net SP growth ≥ `scope_creep_min_growth_pct` (**10%**) vs baseline,
- any issue **added** after baseline,
- any **estimate hike** (baseline SP raised).

**Formula:**

```
growth   = (current_sp - baseline_sp) / baseline_sp * 100
base     = max(growth, min_growth) if (added or hiked) else growth
tp       = time_pressure_multiplier(sprint)
raw      = min(85, base) * tp
score    = max(cap_score(raw), scope_creep_floor_score)   # floor 80 → ALWAYS CRITICAL
```

**Product rule:** *any* confirmed scope creep is a red flag, even +1 SP — the displayed score is
floored at **80** (CRITICAL) (`scope_creep_floor_score`, config.py:115), with the uncapped `raw_score`
still serialized for triage. `confidence: 75` (or **60** if the baseline was captured late, i.e.
> 24h after sprint start).

Worked examples (validate_rubric.py:470):
- Baseline 3 SP, re-estimated to 5 SP, day-2/10 sprint (pressure 0.8):
  `growth = 66.7%`, `raw = min(85, 66.7) × 0.8 = 53.3` → displayed score floored to **80 CRITICAL**.
- No growth / no hikes / no additions → silent.
- Only 1 history point → silent (needs ≥ 2).
- A single +5 SP added issue still floors red.

### 3.9 SPRINT_ENDED_INCOMPLETE (sprint-level) — risk_engine.py:768
**Detector.** Fires when the sprint's end date has passed **but Jira still lists it as active**.
Catches the "no risk shown" gap where every in-flight detector has already gone quiet.

**Trigger.** `now > endDate` (calendar days in the Jira timezone).

**Formula (work remains):**

```
raw = min(100, 60 + days_overdue * 4 + min(remaining_sp, 20) * 1.5)
```

**Formula (all work Done but sprint never closed in Jira):**

```
raw = min(80, 40 + days_overdue * 3)
```

`confidence: 90`. Worked examples (validate_rubric.py:280):
- Ended 1 day ago, 8 of 16 SP remain: `60 + 4 + min(8,20)×1.5 = 76` → **76 HIGH**.
- Ended 1 day ago but all Done: `40 + 3 = 43` → **43 MEDIUM** (still flagged — cleanup risk).
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
   - a `severity_reason` sentence mapping raw → score → severity band, e.g.
     "Why CRITICAL? … → raw 132 → score 100 → CRITICAL (80+)",
   - `factors` / `score_math` blocks carrying the exact multiplier chips the formula used
     (stage ×assignee ×size ×fan-out ×blocking ×trend ×pressure).
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
scope-baseline persistence. Per the repo's convention, backend changes must keep this passing.

The §2.8 refactor ran this suite before and after and must stay byte-identical — current status
**67/67 PASS** (pydantic lives in the venv, so run with `./venv/bin/python validate_rubric.py`).

---

## 7. Review observations (found while tracing)

- `risk_explainer.py` computes rich `factors` / `score_math` / `severity_reason` blocks, but no
  frontend component currently renders `score_math` or `factors`; only `severity_reason` is typed in
  `client.ts` and only the score number + signal facts are displayed in `RiskCardItem`. The "score
  math chip" experience is backend-ready, not yet in the UI.
- `delivery_health` (RAG / `timeline_risk_score`) and `summary.overall_sprint_health` are populated
  in the snapshot but have **no UI component** on the current dashboard (`DashboardHome` renders
  RiskRadar, NextSprintOverview, VelocityTrend only).
- `RISK_TYPE_META` still maps `STALLED_TICKETS` (`🕒 Stalled Tickets`) in `frontend/src/utils/format.ts:58`,
  but the v2 engine no longer emits that type (it was folded into `STORY_NOT_PROGRESSING`).
- `config.py` once shipped a `stalled_base_per_2h = 1.0` knob that no detector ever read (the stalled
  formula hardcodes `h / 2.0`). The §2.8 refactor deleted it — dead config, not a scoring rule.
- These are **display/legacy gaps, not scoring bugs** — do not modify the rubric math for them.