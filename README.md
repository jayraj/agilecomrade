# Agile Comrade — Your sprint companion

React (TypeScript) frontend + FastAPI backend. Each scrum master configures their
own Jira Cloud workspace and LLM provider in a Settings screen; the backend stores
profiles encrypted in **Supabase** and serves the whole dashboard from a single
**cached snapshot**. Scoring uses a transparent **5×5 Probability × Impact risk
matrix** (`backend/risk_matrix.py`).

> 📘 End-user documentation: [User Guide](frontend/public/user-guide.html) · [Privacy Policy](frontend/public/privacy.html)

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
│   ├── crypto.py             # Fernet (AES-128-CBC + HMAC-SHA256) at rest + SHA-256 token hash
│   ├── supabase_store.py     # PostgREST CRUD for `profiles` (service-role only)
│   ├── jira_fetcher.py       # Per-profile Jira fetch + story-points auto-detect + test_connection
│   ├── risk_components.py    # Severity buckets + shared scoring helpers
│   ├── risk_engine.py        # Risk detectors + raw/capped scores + next-sprint
│   ├── mitigation_agent.py   # Per-profile LLM (Gemini | OpenRouter) + fallback
│   ├── snapshot.py           # Builds the single /api/snapshot payload
│   └── validate_rubric.py    # Example assertions for the risk model
└── frontend/                 # Vite React-TS app
    └── src/
        ├── api/config.ts     # localStorage profile slug+token store
        ├── api/client.ts     # Axios client (X-SRR auth headers) + typed endpoints
        ├── hooks/useSnapshot.ts
        ├── utils/format.ts   # Severity colors (80/60/20), risk labels, dates
        └── components/       # RiskRadar, NextSprintOverview, ExecutiveDashboard, SprintOverview, Settings, TopStrip
```

## How multi-tenancy works

- **Profiles** = one row per scrum master in Supabase (`profiles` table):
  Jira URL/email/API token, project keys, LLM provider/model/key (encrypted at
  rest with Fernet (AES-128-CBC + HMAC-SHA256) via `cryptography`), optional
  story-points field override,
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

Scoring is a transparent **5×5 Probability × Impact matrix** (`backend/risk_matrix.py`).
Each detector infers a probability `P` (1–5, how likely the risk is to crystallise)
and an impact `I` (1–5, how bad it is if it does), then:

`matrix_value = P × I` (1–25) → `risk_score = project_matrix(matrix_value)` (0–100)

The projection is band-aligned, so the rounded `risk_score` shown in the UI always
lands in the matching severity band; the continuous `raw_score` is kept for ranking.

**LOW 0–19 · MEDIUM 20–59 · HIGH 60–79 · CRITICAL 80+**

`P` and `I` are inferred from Jira telemetry via explicit, auditable threshold
ladders — see `backend/risk_matrix.py` and the in-app
[User Guide](frontend/public/user-guide.html#scoring).

### Risk types

- **Sprint-level** (radar cards): BURNDOWN_BEHIND, QA_BOTTLENECK, DUE_DATE_PASSED,
  SCOPE_CREEP, SPRINT_ENDED_INCOMPLETE
- **Ticket-level** (blockers panel): STORY_NOT_PROGRESSING, EXTERNAL_DEPENDENCY,
  BUG_RAISED, OVERLOADED
- **Next-sprint signals** (count-based, not matrix-scored): UNASSIGNED, UNESTIMATED,
  UNDEFINED_SCOPE, SIZING_RISK

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