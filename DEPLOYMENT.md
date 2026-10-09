# Deployment Guide — Agile Comrade

Deploy the monorepo as **two independent Vercel projects** (backend API +
frontend SPA). Both are deployed from the same Git repository using Vercel's
**Root Directory** setting — no code duplication, no shared build.

```
GitHub repo ──┬── Vercel Project #1 "srr-backend"   (Root Directory: backend)
              │     Python serverless function  →  https://<backend>.vercel.app
              │
              └── Vercel Project #2 "srr-frontend"  (Root Directory: frontend)
                    Vite static SPA             →  https://<frontend>.vercel.app
                            │
                            └── fetch() calls https://<backend>.vercel.app/api/*
                                    │
                                    └── Supabase (profiles, encrypted Jira creds, snapshots)
```

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| GitHub account | This repo pushed to GitHub |
| [Vercel](https://vercel.com) account | Free Hobby plan works (60 s function cap configured) |
| [Supabase](https://supabase.com) project | Stores profiles + snapshots |
| Jira Cloud API token | Create at `id.atlassian.com/manage-profile/security/api-tokens` |
| LLM API key | Gemini (`aistudio.google.com`) **or** OpenRouter — optional; rule-based fallback exists |

## 2. Supabase setup

1. Create a project, open **SQL Editor**, run the contents of [`sql/migration.sql`](sql/migration.sql)
   (creates the `profiles` table with RLS).
2. Collect from **Project Settings → API**:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key (secret!) → `SUPABASE_SERVICE_ROLE_KEY`

> The backend talks to PostgREST with the service-role key only; RLS stays on
> and end users never hold this key.

## 3. Secrets

Generate the encryption key used to encrypt Jira credentials at rest:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store the output — you'll paste it as `ENCRYPTION_KEY` below. **Rotating this
key later invalidates all stored profile secrets.**

Never commit `.env` files — they are gitignored. All production config lives in
Vercel Environment Variables.

## 4. Backend — Vercel Project #1

1. Push this repo to GitHub.
2. In Vercel: **Add New… → Project** → import the repo.
3. Under **Root Directory**, enter `backend`. Framework Preset: **Other**
   (Vercel auto-detects the Python runtime via [`backend/api/index.py`](backend/api/index.py)).
4. Add Environment Variables:

| Variable | Required | Example / how to get it |
|---|---|---|
| `ENCRYPTION_KEY` | ✅ | Fernet key from step 3 |
| `SUPABASE_URL` | ✅ | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | service-role secret from step 2 |
| `CORS_ORIGINS` | ✅ | Comma-separated origins: `https://<frontend>.vercel.app,http://localhost:3001` |
| `JIRA_CLOUD_URL` | – | Default shown on the Settings screen (`https://<site>.atlassian.net`) |
| `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECTS` | – | Optional defaults; each profile supplies its own via the UI |
| `LLM_PROVIDER` | – | `gemini` (default) or `openrouter` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | – | e.g. `gemini-flash-latest` |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | – | e.g. `openai/gpt-4o-mini` |

5. Deploy. Verify:

```bash
curl https://<backend>.vercel.app/api/health
# {"status":"healthy","storage":"supabase", ...}
```

Config notes:
- [`backend/vercel.json`](backend/vercel.json) routes `/api/*` to the function and sets `maxDuration: 60`.
- [`backend/.vercelignore`](backend/.vercelignore) keeps `venv/`, `data/`, logs out of the lambda.
- Dependencies come from [`backend/requirements.txt`](backend/requirements.txt) (pinned versions).

## 5. Frontend — Vercel Project #2

1. Import the **same repo** again as a new project.
2. **Root Directory**: `frontend`. Framework Preset: **Vite** (build `npm run build`,
   output `dist` — detected automatically).
3. Add one Environment Variable:

| Variable | Required | Value |
|---|---|---|
| `VITE_API_BASE` | ✅ | `https://<backend>.vercel.app` (project #1's URL, no trailing slash) |
| `VITE_SHOW_AI_DEBUG` | – | `true` shows the "🔍 View AI Prompt & Raw Response" sections. Unset in prod (default hidden); local dev sets it in `.env.development` |

> ⚠️ Without `VITE_API_BASE`, production builds fall back to same-origin `/api`
> and every request 404s. See `API_BASE` in [`frontend/src/api/client.ts`](frontend/src/api/client.ts).

4. Deploy.

## 6. Post-deploy checklist

- [ ] `curl https://<backend>.vercel.app/api/health` → `"status": "healthy"`
- [ ] Open `https://<frontend>.vercel.app` → dashboard renders (empty state OK)
- [ ] Browser DevTools → Network: requests go to `https://<backend>.vercel.app/api/...` (not `/api` same-origin)
- [ ] No CORS errors in console (if red: re-check `CORS_ORIGINS` includes the exact frontend URL)
- [ ] Settings → enter Jira site/email/token → **Test Connection** ✓ → Save
- [ ] Dashboard → **Sync Now** → risks appear
- [ ] Sprint page → **Run AI Scan** → mitigation plan renders

## 7. Local development

```bash
# Terminal 1 — backend (port 5002)
cd backend
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp ../.env.example .env          # fill ENCRYPTION_KEY, SUPABASE_*, CORS_ORIGINS
./venv/bin/python main.py

# Terminal 2 — frontend (port 3001)
cd frontend
npm install
npm run dev                      # no VITE_API_BASE needed; dev defaults to :5002
```

Sanity checks: `python3 -m pytest` and `python3 validate_rubric.py` in `backend/` (67/67),
`npm run lint && npm run typecheck && npm run build` in `frontend/`.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Frontend calls return 404 on `/api/*` in production | `VITE_API_BASE` missing on the frontend project — set it and redeploy |
| CORS error in browser console | `CORS_ORIGINS` on the backend doesn't include the exact frontend origin (scheme + host, no trailing slash) |
| `502` from `/api/test-config` or other endpoints | Upstream Jira rejected the request — check the Jira email/token for that profile |
| `429 Too Many Requests` | Per-IP rate limiting is intentional (`/api/test-config`: 10 / 5 min, comments: 20 / 5 min) — wait and retry |
| First snapshot load is slow (~10–30 s) | Serverless cold start + full Jira fetch; subsequent calls hit the cached snapshot. Hobby cap is 60 s (`maxDuration`) |
| `{"status":"error","storage":...}` on health | Supabase URL / service-role key wrong or migration not run |
| Profile save fails after rotating `ENCRYPTION_KEY` | Expected — old ciphertext can't decrypt. Delete affected profiles and re-create them |

## 9. CI / redeploys

Both projects auto-redeploy on every push to the tracked branch. Deploy them
independently — a frontend-only change never touches the backend lambda and
vice versa.
