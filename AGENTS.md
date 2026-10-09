# System Guidelines

Rules and guidelines to follow when generating code for the Agile Comrade app.

# General guidelines

- Prefer responsive, well-structured layouts using flexbox and CSS grid over absolute positioning.
- Use plain, named CSS classes in `frontend/src/index.css` — do **not** use Tailwind utility classes, even though Tailwind is installed.
- Keep components in `frontend/src/components/` (one component per file) and helpers in `frontend/src/utils/format.ts`.
- Reuse existing UI components and CSS classes; don't duplicate styles or components.
- Icons are lucide-react (`strokeWidth={2}` default) sized via the `size` prop: 16 inside buttons, 20 inline, 24 for section titles. Default color `#52525b`. Emojis are acceptable for UI labels/titles.
- After any change, run `npm run lint && npm run typecheck && npm run test && npm run build` in `frontend/`; backend changes must pass `python3 -m pytest` and `python3 validate_rubric.py` in `backend/`. CI (`.github/workflows/ci.yml`) runs these same gates on every PR.
- Backend risk data flows through `backend/risk_engine.py`, `backend/snapshot.py`, and the frontend `Blocker` type in `frontend/src/api/client.ts`.

# Design system guidelines

The app follows a token-based design system defined as CSS variables in the `:root` block of `frontend/src/index.css` (fonts, color ramps, spacing, radius, shadows, borders, icon and table tokens). Always consume tokens via `var(--…)` — never hardcode hex values in component styles.

## Colors

- **Severity (primary semantic color)** — always from the shared helpers in `frontend/src/utils/format.ts` (`getRiskColor`, `severityColor`, `ringRiskColor`, `severityClass`):
  - CRITICAL → `#ef4444` (error red)
  - HIGH → `#d97706` (deepened warning)
  - MEDIUM → `#f59e0b` (warning amber)
  - LOW → `#10b981` (success green)
- **Primary** — blue ramp; primary actions/buttons use `--color-primary-600` (`#2563eb`).
- **Accent / CTA** — emerald `--color-accent-500` (`#10b981`) for the Sync CTA.
- **Neutrals** — text `var(--color-secondary-800/700)`, table/body text `var(--color-neutral-600)` (`#52525b`), muted `var(--color-neutral-500/400)`, borders `var(--border-color)` (`#e4e4e7`), surfaces `var(--bg-surface)` (`#f8fafc`).
- Card border-left color is severity-driven: severity classes on `.risk-card`, and future-sprint cards set it inline from `getRiskColor(risk_score)` so it always matches the score badge.

## Typography

- Font family: Inter (`--font-heading/--font-body`, loaded in `index.html`); no monospace fonts anywhere.
- Headings: 700 weight, `-0.02em` letter spacing, line-height 1.2; section titles `.component-title` ~24px.
- Body text 14–16px; table header 13px/600 `--table-header-text`; table rows 14px `--table-row-text`.
- Dates always formatted as "Jun 10" via `formatDate` in `frontend/src/utils/format.ts`.

## Layout & spacing

- Spacing scale tokens `--spacing-xs…3xl` (4/8/16/24/32/48/64px); radius tokens `--radius-sm/md/lg/xl/full`; shadows `--shadow-subtle/medium/large`.
- `radar-grid` for card grids: `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))`.
- Cards: white `background`, `border-radius: var(--radius-md)` (8px), `padding: 16px`, `border-left: 4px solid` severity color, `box-shadow: var(--shadow-subtle)`.
- Panel (`blockers-panel`): white, radius `var(--radius-md)`, padding 20px.
- Page headers row uses `.panel-heading` (flex, title left / action right).

## Components

### Button
Buttons trigger actions (sync, scan, mitigate, draft, navigate). Labels are short and action-oriented.

#### Variants
- **Primary** — `.ai-scan-btn`: solid `--color-primary-600`, white text, radius `--radius-md`, padding `10px 20px`, 15px/600, subtle shadow; hover `primary-700`, active `primary-800`, focus ring `primary-300`, disabled `primary-300` @60%. Used for "Run AI Scan" / "Mitigate with AI".
- **CTA** — `.sync-btn`: emerald `--color-accent-500` with colored glow shadow; hover `accent-600`, active `accent-700`, focus ring `accent-300`. Used for "Sync Now".
- **Secondary** — `.details-btn`: slate filled (`secondary-100` bg, `secondary-300` border, `secondary-700` text); hover darkens + slight scale. Used for card actions ("Details →").
- **Tertiary / Utility** — `.draft-btn`, `.copy-btn`, `.post-btn`, `.delete-btn`: small supporting buttons using neutral or primary tint tokens.
- All buttons follow the guide states: hover color shift, active darken, visible focus ring, disabled ≥0.6 opacity + `cursor: not-allowed`.

### Risk card
Structure: `.risk-header` (risk-score badge + `.issue-key`), `.risk-body` (`.summary` with optional `.sprint-dates`, `.risk-type`, `.sprint-meta`, `.sprint-day`), `.risk-footer` (action button).

### Risk table (severity-filtered)
- Tabs: `.risk-tabs` → `.risk-tab` (muted), `.risk-tab.active` (primary-blue text + blue 2px bottom border).
- Table: `.risk-table` styled to the table tokens (bordered wrapper, radius 8, subtle shadow, `--table-header-bg` header band, zebra rows `--table-alt-row-bg`, hover `--table-hover-bg`); rows stay CSS-grid based for expandable rows (5-col default).
- Each row: `.risk-row-main` (`.risk-dot` pulsing for CRITICAL/MEDIUM + `.risk-row-title` with `.blocker-key` + `.risk-row-summary`), then `.risk-row-cat`, `.risk-row-detect`, `.risk-row-stories`, `.risk-row-status`.
- Status labels by severity: CRITICAL/HIGH → `ACTIVE` (severity color), MEDIUM → `MONITORING` (amber), LOW → `MITIGATED` (green).
- Expanding a row shows `.risk-row-expand` with the AI mitigation strategy recommendation.

### Breadcrumb
`.breadcrumb` → `Link` / `.breadcrumb-link-btn` / `.breadcrumb-sep` ( `/` ) / `.breadcrumb-current`; links use `--color-primary-600`.

### Other
- Badges: `.risk-type-badge.critical/.high/.medium/.low` (tinted backgrounds matching severity families).
- Chips: `.risk-chip` (mitigation).
- Empty states: `.empty-cell` (✅ no blockers) and `.risk-table-empty`.
- Home tabs: `.home-tab-bar` / `.home-tab` / `.home-tab.active`.
