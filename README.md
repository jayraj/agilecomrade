# Agile Comrade — Your sprint companion

React (TypeScript) frontend + FastAPI backend. Each scrum master configures their
own Jira Cloud workspace and LLM provider in a Settings screen; the backend stores
profiles encrypted in **Supabase** and serves the whole dashboard from a single
**cached snapshot**. Scoring uses a transparent **risk scoring model**
(`severity × time-pressure × blast-radius`).

> 📘 End-user documentation: [docs/USER_MANUAL.html](docs/USER_MANUAL.html)

## Architecture

```
agilecomrade/
├── sql/migration.sql         # profiles table + RLS (run in Supabase SQL editor)
├── backend/                  # FastAPI app (token-gated)
│   ├── vercel.json           # /api/* -> Python function
│   ├── .vercelignore         # keeps venv/data out of the lambda
│   ├── requirements.txt      # Python deps
│   ├── api/index.py          # Serverless shim -> main.app
│   ├── main.py               # FastAPI app
│   ├── config.py             # Settings (env) + UserConfig (per-profile)
│   ├── crypto.py             # AES-GCM (Fernet) at rest + SHA-256 token hash
│   ├── supabase_store.py     # PostgREST CRUD for `profiles` (service-role only)
│   ├── jira_fetcher.py       # Per-profile Jira fetch + story-points auto-detect + test_connection
│   ├── risk_components.py    # Shared scoring factors (time pressure, stage, size, …)
│   ├── risk_engine.py        # Risk detectors + raw/capped scores + next-sprint
│   ├── mitigation_agent.py   # Per-profile LLM (Gemini | OpenRouter) + fallback
│   ├── snapshot.py           # Builds the single /api/snapshot payload
│   └── validate_rubric.py    # Example assertions for the risk model
└── frontend/                 # Vite React-TS app
    └── src/
        ├── api/config.ts     # localStorage profile slug+token store
        ├── api/client.ts     # Axios client (X-SRR auth headers) + typed endpoints
        ├── hooks/useSnapshot.ts
        ├── utils/format.ts   # Severity colors (70/35), risk labels, dates
        └── components/       # RiskRadar, NextSprintOverview, ExecutiveDashboard, SprintOverview, Settings, TopStrip
```

## How multi-tenancy works

- **Profiles** = one row per scrum master in Supabase (`profiles` table):
  Jira URL/email/API token, project keys, LLM provider/model/key (encrypted at
  rest with AES-GCM via `cryptography`), optional story-points field override,
  cached `snapshot jsonb`, `burndown_history jsonb`, `fetched_at`.
- **No login (MVP)**. Each profile has a slug + access token. The token is
  generated in the browser, saved in `localStorage` (`srr2_profiles`), and the
  backend stores **only a SHA-256 hash** of it. Requests send
  `X-SRR-Profile` + `X-SRR-Token` headers.
- **Backend owns Supabase** via the service-role key; the frontend never talks to
  Supabase directly. RLS is enabled with **no anon policies**.
- **Serverless-friendly**: no scheduler. `/api/snapshot` returns the cached
  snapshot when it's under `SYNC_INTERVAL_MINUTES` (default 5 min) old, otherwise
  re-fetches Jira, recomputes risks, and persists a new snapshot. Burndown
  history lives in the profile row.

## Setup

### 1. Supabase

1. Create a Supabase project and run `sql/migration.sql` in the SQL editor.
2. Grab the project URL and the **service-role** key (Settings → API).

### 2. Environment variables

Copy `.env.example` and fill in (local: `backend/.env`):

```
ENCRYPTION_KEY=            # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001,https://<frontend>.vercel.app
```

The `JIRA_*` / `GEMINI_*` / `OPENROUTER_*` vars are only **defaults** shown on the
Settings screen — each profile supplies its own via the UI.

### 3. Run locally

```bash
# Backend (port 5002)
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest              # unit tests
python validate_rubric.py     # 67/67 rubric checks
python main.py

# Frontend (port 3001) — point it at the local backend
cd frontend
cp .env.example .env.local    # VITE_API_BASE=http://127.0.0.1:5002
npm install
npm run test                  # unit tests (vitest)
npm run dev
```

Open `http://localhost:3001`, go to **Settings**, create a profile (Jira Cloud URL,
email, API token, project keys, LLM provider/model/key). "Test Connection" validates
it without saving. The generated access token is stored in your browser only.

## Risk scoring model

Every risk starts from a base severity, then scales with schedule pressure and the
number of teams/items it can affect:

`score = base_severity × time_pressure_multiplier × blast_radius_weight`

The UI caps the result at 100 (`risk_score`); the uncapped value is also kept for
ranking. Final buckets (frontend colors use the same thresholds):

**LOW < 35 · MEDIUM 35–69 · HIGH ≥ 70**

### Inputs

**Time pressure** — driven by the share of sprint time already elapsed:

| Time elapsed | Multiplier |
|--------------|-----------|
| 0–25%        | 0.6       |
| 25–50%        | 0.8       |
| 50–75%        | 1.1       |
| 75–90%        | 1.4       |
| 90–100%      | 1.7       |

**Workflow stage weight**: To Do 0.6 · In Progress 0.9 · Code Review 1.1 · QA/In QA
Review 1.3 · Blocked 1.4.

**Issue size**: `0.7 + (sp / avg_sprint_sp) × 0.3`, clamped to 0.4–1.6.

### Risk types

- **Sprint-level** (radar cards): BURNDOWN_BEHIND, QA_BOTTLENECK, DUE_DATE_PASSED
- **Ticket-level** (blockers panel): STORY_NOT_PROGRESSING, EXTERNAL_DEPENDENCY

## API endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /api/health` | – | Liveness + storage status |
| `GET /api/config-defaults` | – | Provider options + default models |
| `POST /api/profiles` | – | Create profile (returns access token once) |
| `POST /api/profiles/verify` | – | Validate slug + token |
| `GET/PUT/DELETE /api/profiles/{slug}` | profile | Read (sanitized) / update / delete |
| `POST /api/test-config` | – | Validate a config without saving |
| `GET /api/snapshot` | profile | Single dashboard payload (cached) |
| `POST /api/sync-now` | profile | Force a fresh fetch + recompute |
| `POST /api/generate-mitigations` | profile | Sprint-level AI mitigation plan |
| `POST /api/next-sprint-risks` | profile | Pre-planning AI risk analysis |
| `POST /api/next-sprint-issues` | profile | Planned work items |
| `POST /api/generate-followup-message` | profile | Draft a follow-up to an assignee |