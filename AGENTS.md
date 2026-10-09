# System Guidelines

Rules and guidelines to follow when generating code for the Agile Comrade app.

# General guidelines

- Prefer responsive, well-structured layouts using flexbox and CSS grid over absolute positioning.
- Use plain, named CSS classes in `frontend/src/index.css` — do **not** use Tailwind utility classes, even though Tailwind is installed.
- Keep components one-per-file: shared chrome in `frontend/src/components/`, feature-specific UI co-located under `frontend/src/features/<feature>/`; helpers in `frontend/src/utils/` (`format.ts`, `severity.ts`).
- Reuse existing UI components and CSS classes; don't duplicate styles or components.
- Icons are lucide-react (`strokeWidth={2}` default) sized via the `size` prop: 16 inside buttons, 20 inline, 24 for section titles. Default color `#52525b`. Emojis are acceptable for UI labels/titles.
- After any change, run `npm run lint && npm run typecheck && npm run test && npm run build` in `frontend/`; backend changes must pass `python3 -m pytest` and `python3 validate_rubric.py` in `backend/`. CI (`.github/workflows/ci.yml`) runs these same gates on every PR.
- Backend risk data flows through `backend/risk_engine.py`, `backend/snapshot.py`, and the frontend `Blocker` type in `frontend/src/api/types.ts`.

# Design system guidelines

The app follows a token-based design system defined as CSS variables in the `:root` block of `frontend/src/index.css` (typography, color ramps, spacing, radius, shadows, borders, badge/icon tokens). Always consume tokens via `var(--…)` — never hardcode hex values in component styles.

## Colors

- **Severity** — derive from the shared helpers in `frontend/src/utils/severity.ts` (`getRiskColor(score)`, `severityFromScore(score)`):
  - CRITICAL → `#ef4444` (error red), HIGH → `#d97706`, MEDIUM → `#f59e0b`, LOW → `#10b981`.
  - Severity strings are uppercased for display; CSS class suffixes `.critical/.high/.medium/.low` style cards and badges.
- **Primary** — blue ramp; primary actions use `--color-primary-600` (`#2563eb`).
- **Accent / CTA** — emerald `--color-accent-500` (`#10b981`) for the Sync CTA.
- **Neutrals** — text `var(--color-secondary-800/700)`, body/table text `var(--color-neutral-600)`, muted `var(--color-neutral-500/400)`, borders `var(--border-color)` (`#e4e4e7`), surfaces `var(--bg-surface)` (`#f8fafc`).
- Sprint cards tint their border/badge by severity (`.sprint-card` severity classes); future-sprint cards use `getRiskColor(risk_score)` inline so the border matches the score badge.

## Typography

- Font family: Inter (`--font-heading`/`--font-body`, loaded in `index.html`).
- Headings: 700 weight, `--letter-spacing` (`-0.02em`), `--heading-line-height` (1.2).
- `--font-mono` (JetBrains Mono) is reserved for numeric / eyebrow / day-badge / points text — not prose.
- Dates always formatted via `formatDate` in `frontend/src/utils/format.ts` (e.g. "Jun 10").

## Layout & spacing

- Spacing scale tokens `--spacing-xs…3xl`; radius tokens `--radius-sm/md/lg/xl/full`; shadows `--shadow-subtle/medium/large`.
- Card grids use `.sprint-card-grid` (`repeat(auto-fit, minmax(...))`).
- Cards: white background, `border-radius: var(--radius-md)`, severity-colored accent.
- Detail views are assembled from `.sprint-detail-*` sections.

## Components

### Buttons
Buttons trigger actions (sync, scan, mitigate, draft, adopt a decision). Labels are short and action-oriented.

- **Primary** — `.ai-scan-btn`: solid `--color-primary-600`, white text, `--radius-md`; hover `primary-700`, active `primary-800`, focus ring `primary-300`, disabled `primary-300` @60%. Used for "Run AI Scan" / "Mitigate with AI".
- **CTA** — `.sync-btn`: emerald `--color-accent-500` with glow shadow; hover `accent-600`, active `accent-700`, focus ring `accent-300`. Used for "Sync Now".
- **Utility** — `.draft-btn`, `.copy-btn`, `.risk-decision-btn` (`.risk-decision-btn--active` marks the selected ROAM disposition).
- All buttons: hover color shift, active darken, visible focus ring, disabled ≥0.6 opacity + `cursor: not-allowed`.

### Risk card (`.risk-card-item`)
Severity-tinted card built from `.risk-card-item-header`, `.risk-card-item-title`, an optional `.risk-signal` block (score-driver chips + fact list), AI cause/action, and a `.risk-decision-*` ROAM control. Decision statuses: `resolved` / `owned` / `accepted` / `mitigated`.

### Sprint card (`.sprint-card`)
Dashboard/radar card: `.sprint-card-badge`, `.sprint-card-grid` container, plus gauge/progress classes. Rendered by `RiskRadar`.

### Other
- `.risk-chip` — mitigation type chips.
- `.no-data` / `.no-data-soft` — empty and loading-soft states.
- Severity badges use the `--badge-*` tokens per `critical/high/medium/low`.
