# Risk Score Transparency (A + C)

## Decisions (locked)
- **A**: Show the visible score math on every card — collapsed on the dashboard, expanded in the detail drawer.
- **C**: Always-on band legend (severity bands + ranges) + legend in register header.
- Keep the existing `severity_reason` tooltip as a complement (not the only window).
- Register was **rolled back** (per user); do NOT rebuild it. This plan excludes register work entirely.

## Scope (frontend-only, no backend changes → rubric 67/67 stays green)

### New shared logic — `frontend/src/utils/format.ts`
Add two pure helpers:
- `scoreDrivers(risk) → ScoreDriver[]` — ordered driver chips built from **structured factor fields** already on the payload (`stage_weight`, `assignee_factor`, `size_weight`, `assignee_count`, `fan_out`, `blocking_factor`, `trend_factor`, `time_pressure_multiplier`, plus raw facts `hours_since_update`, `days_overdue`, `days_remaining`, `story_points`). Never parse the English sentence. Returns `[]` for pure-count risks → UI falls back to `severity_reason`.
- `riskScoreBand(score) → { min; max; band }` (reuse existing band constants).

### New component — `frontend/src/components/RiskScoreLegend.tsx`
Reusable one-liner legend: `🟢 LOW <20 · 🟡 MEDIUM 20–59 · 🟠 HIGH 60–79 · 🔴 CRITICAL 80+` with color dots (severity-colored). Used by `RiskRadar` and `RiskRegister` header area.

### Cards
- `RiskCardItem.tsx` (detail, always expanded): render score band line (`Severity (band) · score N`, severity-colored) + `<ScoreDrivers>` chips row under the title. Keep tooltip.
- `SprintCard.tsx` (dashboard, collapsed by default): compact `Σ N drivers → score N` with chevron; expand to reveal `<ScoreDrivers>` chips. Reuse `RiskCardItem`'s helper/component.

### Dashboard + register
- `RiskRadar.tsx`: render `<RiskScoreLegend />` under the section header.
- `RiskRegister.tsx`: render `<RiskScoreLegend />` in register header (next to CSV button); Severity cell gets `title` = latest `severity_reason` on hover.

### Styles — `frontend/src/index.css`
Reusable classes from existing tokens: `.score-band`, `.risk-driver-chips`, `.risk-driver-chip`, `.risk-score-legend`, `.risk-score-legend-chip`, register legend wrapper, collapsible chevron/expand styles.

## Files
1. `frontend/src/utils/format.ts` — helpers.
2. `frontend/src/components/RiskScoreLegend.tsx` — new.
3. `frontend/src/components/RiskCardItem.tsx` — score band + chips.
4. `frontend/src/components/SprintCard.tsx` — collapsible driver summary.
5. `frontend/src/components/RiskRadar.tsx` — legend under header.
6. `frontend/src/components/RiskRegister.tsx` — legend + severity hover title.
7. `frontend/src/index.css` — styles.

## Verification
- `npm run lint && npm run build` in `frontend/` (clean).
- Backend untouched → `backend/validate_rubric.py` stays 67/67.

## Commit
Single combined commit (per "Approve + single combined commit"): adjust message to describe "visible risk score math + band legend + severity tooltips". Do NOT push.

## Out of scope (offer later)
- B — full "how scores work" explainer pages.
- Risk Register rebuild (decided rollback).
- Any frontend score recomputation.
