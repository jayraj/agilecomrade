import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import quote as urlquote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import UserConfig, settings
from crypto import decrypt, encrypt, sha256_hex
from jira_fetcher import JiraFetcher, fetch_all
from mitigation_agent import MitigationAgent
from risk_components import now_utc, to_utc
from risk_engine import RiskEngine
from risk_explainer import DECISION_STATUSES, explain_risk
from snapshot import build_snapshot
from supabase_store import DuplicateProfileError, SupabaseStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Interactive API docs (/docs, /redoc, /openapi.json) are disabled in production
# deployments (Vercel sets VERCEL_ENV=production; or set ENVIRONMENT=production).
_IS_PROD = (
    os.getenv("VERCEL_ENV") == "production"
    or os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}
)

app = FastAPI(
    title="Agile Comrade API",
    version="3.0.0",
    description="Multi-scrum-master SaaS API (profiles in Supabase, serverless on Vercel)",
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error": "Internal server error"},
    )


store = SupabaseStore()

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

JIRA_URL_RE = re.compile(r"^https://[a-z0-9][a-z0-9-]*\.atlassian\.net$")

# Simple in-memory rate limiter (per key: list of timestamps)
_RATE_BUCKETS: dict = {}
_RATE_LOCK = threading.Lock()


def rate_limit(key: str, max_requests: int, window_seconds: int):
    """Returns None if allowed, or a JSONResponse with 429 if over the limit."""
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
        if len(bucket) >= max_requests:
            return JSONResponse(
                {"status": "error", "error": "Too many requests. Please try again later."},
                status_code=429,
            )
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket
    return None


def validate_jira_url(url: str) -> bool:
    """Only https://<site>.atlassian.net URLs are accepted (blocks SSRF to internal hosts)."""
    u = (url or "").strip().rstrip("/").lower()
    return bool(JIRA_URL_RE.match(u))


def _safe_upstream_error(detail: str) -> str:
    """Map upstream error fragments to safe client-facing messages."""
    d = (detail or "").lower()
    if "401" in d or "unauthorized" in d or "403" in d or "forbidden" in d:
        return "Authentication failed — check email / API token"
    if "404" in d or "not found" in d:
        return "Resource not found — check the URL and project keys"
    if "timed out" in d or "timeout" in d:
        return "Connection timed out"
    return "Request failed — verify the configuration"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

# UserConfig field name -> profile column name (empty value => plain column,
# "_enc" suffix columns are written encrypted).
CONFIG_FIELD_MAP = {
    "jira_cloud_url": "jira_cloud_url",
    "jira_email": "jira_email",
    "jira_api_token": "jira_api_token_enc",
    "jira_projects": "project_keys",
    "llm_provider": "llm_provider",
    "llm_model": "llm_model",
    "llm_api_key": "llm_api_key_enc",
    "story_points_field": "story_points_field",
}


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
def _auth(request: Request):
    """Resolve the profile from X-SRR-Profile + X-SRR-Token headers.

    Returns (profile_row, None) on success or (None, JSONResponse) on failure.
    Always responds 401 on failure so invalid slugs cannot be enumerated.
    """
    slug = (request.headers.get("X-SRR-Profile") or "").strip()
    token = (request.headers.get("X-SRR-Token") or "").strip()
    if not slug or not token or len(token) < 16 or not _validate_slug(slug):
        return None, JSONResponse(
            {"status": "error", "error": "Invalid credentials"},
            status_code=401,
        )
    try:
        row = store.get_profile(urlquote(slug, safe=""))
    except Exception as e:
        logger.error(f"Supabase lookup failed for {slug}: {e}")
        return None, JSONResponse({"status": "error", "error": "Storage unavailable"}, status_code=503)

    if not row or not hmac.compare_digest(row.get("access_token_hash", ""), sha256_hex(token)):
        return None, JSONResponse({"status": "error", "error": "Invalid credentials"}, status_code=401)

    return row, None


def _config_from_body(body: dict) -> UserConfig:
    body = body or {}
    return UserConfig(
        jira_cloud_url=(body.get("jira_cloud_url") or "").strip(),
        jira_email=(body.get("jira_email") or "").strip(),
        jira_api_token=(body.get("jira_api_token") or "").strip(),
        jira_projects=(body.get("jira_projects") or "").strip(),
        llm_provider=(body.get("llm_provider") or "gemini").strip(),
        llm_model=(body.get("llm_model") or "").strip(),
        llm_api_key=(body.get("llm_api_key") or "").strip(),
        story_points_field=(body.get("story_points_field") or "").strip(),
    )


def _validate_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or ""))


def _sanitized_config(row: dict, config: UserConfig) -> dict:
    return {
        "slug": row.get("slug"),
        "jira_cloud_url": config.jira_cloud_url,
        "jira_email": config.jira_email,
        "jira_projects": config.jira_projects,
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "story_points_field": config.story_points_field,
        "fetched_at": row.get("fetched_at"),
    }


def _trim_issue(issue: dict) -> dict:
    """Strips bulky fields from stored issues (changelog; long free text)."""
    trimmed = dict(issue)
    trimmed.pop("changelog", None)
    if isinstance(trimmed.get("description"), str):
        trimmed["description"] = trimmed["description"][:500]
    if isinstance(trimmed.get("acceptance_criteria"), str):
        trimmed["acceptance_criteria"] = trimmed["acceptance_criteria"][:500]
    return trimmed


def _trim_sprint_data(data: dict) -> dict:
    out = {}
    for project_key, bundle in data.items():
        out[project_key] = {
            "sprint": bundle.get("sprint"),
            "issues": [_trim_issue(i) for i in bundle.get("issues", [])],
        }
    return out


def _refresh_snapshot(row: dict, config: UserConfig):
    """Fetch fresh Jira data, recompute risks, persist snapshot + history.

    If the Jira fetch comes back completely empty (e.g. transient outage),
    the previously persisted snapshot is kept instead of wiping it.
    """
    logger.info(f"🔄 Fetching Jira data for profile '{row['slug']}'...")
    fetcher = JiraFetcher(config)
    data = fetch_all(fetcher)
    sprint_data = _trim_sprint_data(data["sprint_data"])
    next_sprint_data = _trim_sprint_data(data["next_sprint_data"])
    velocity_data = data["velocity_data"]
    # The Jira timezone (from /myself) is the single source of truth for
    # calendar-day math, so our dates match what the user sees in Jira —
    # regardless of where this server runs.
    jira_timezone = data.get("jira_timezone")

    if not sprint_data and not next_sprint_data:
        stale = row.get("snapshot")
        if stale:
            logger.warning(f"⚠️ Empty Jira fetch for '{row['slug']}' — keeping previous snapshot.")
            return stale

    burndown_history = row.get("burndown_history") or {}
    # Scope-creep state lives inside the persisted snapshot (no dedicated
    # Supabase column): baselines captured at first active sync + SP trail.
    # Human risk decisions are carried the same way so they survive re-syncs.
    prev_snapshot = row.get("snapshot") or {}
    scope_meta = prev_snapshot.get("scope_meta") or {"baselines": {}, "history": {}}
    risk_decisions = prev_snapshot.get("risk_decisions") or {}

    risk_engine = RiskEngine()

    # Append today's burndown gaps (capped history) before risk calc so the
    # trend factor sees the latest check-in.
    for project_data in sprint_data.values():
        sprint = project_data.get("sprint")
        if not sprint:
            continue
        gap = risk_engine.get_burndown_gap(sprint, project_data.get("issues", []))
        if gap is None:
            continue
        name = sprint.get("name")
        history = burndown_history.setdefault(name, [])
        history.append(gap["burndown_gap_percent"])
        if len(history) > settings.burndown_history_size:
            del history[: len(history) - settings.burndown_history_size]

    # Capture/extend scope-creep state for each ACTIVE sprint: baseline on
    # first sight (the planning commitment), then append today's total SP.
    now_iso = datetime.utcnow().isoformat()
    now = now_utc()
    for project_data in sprint_data.values():
        sprint = project_data.get("sprint")
        if not sprint or not sprint.get("name"):
            continue
        name = sprint["name"]
        issues = project_data.get("issues", [])
        total_sp = sum(i.get("story_points", 0) or 0 for i in issues)
        baselines = scope_meta.setdefault("baselines", {})
        if name not in baselines:
            start = to_utc(sprint.get("startDate"))
            late = bool(
                start and (now - start).total_seconds() > settings.scope_baseline_grace_hours * 3600
            )
            baselines[name] = {
                "total_sp": total_sp,
                "issues": {
                    i.get("key"): (i.get("story_points", 0) or 0)
                    for i in issues
                    if i.get("key")
                },
                "captured_at": now_iso,
                "late_capture": late,
            }
            if late:
                logger.info(f"📅 Late scope baseline for '{name}' ({total_sp} SP) — lower confidence.")
            else:
                logger.info(f"📅 Scope baseline captured for '{name}' ({total_sp} SP).")
        trail = scope_meta.setdefault("history", {}).setdefault(name, [])
        trail.append(total_sp)
        if len(trail) > settings.scope_history_size:
            del trail[: len(trail) - settings.scope_history_size]
        base = baselines[name]
        adds_now = sum(1 for i in issues if i.get("key") and i["key"] not in (base.get("issues") or {}))
        logger.info(
            f"🔭 Scope[{name}] baseline={base.get('total_sp')}SP current={total_sp}SP "
            f"trail={trail} adds_vs_baseline={adds_now} manual={base.get('manual', False)}"
        )

    risks = risk_engine.calculate_all_risks(
        sprint_data,
        velocity_data=velocity_data,
        burndown_history=burndown_history,
        scope_meta=scope_meta,
        jira_timezone=jira_timezone,
    )

    snapshot = build_snapshot(
        sprint_data=sprint_data,
        next_sprint_data=next_sprint_data,
        velocity_data=velocity_data,
        risks=risks,
        burndown_history=burndown_history,
        mitigations=[],
        last_sync=datetime.utcnow().isoformat(),
        scope_meta=scope_meta,
        jira_timezone=jira_timezone,
        risk_decisions=risk_decisions,
    )

    store.update_profile(row["slug"], {
        "snapshot": snapshot,
        "burndown_history": burndown_history,
        "fetched_at": datetime.utcnow().isoformat(),
    })

    logger.info(f"✅ Profile '{row['slug']}' synced. {len(risks)} risks.")
    return snapshot


def _get_or_refresh_snapshot(row: dict, allow_stale: bool = False):
    config = UserConfig.from_row(row, decrypt)
    fetched_at = row.get("fetched_at")
    snapshot = row.get("snapshot")

    if snapshot:
        # AI generation doesn't need freshly-fetched Jira data — the page the
        # user is acting on already reflects the current snapshot. Reusing it
        # avoids a full Jira refetch on every "Mitigate/Scan/draft" click.
        if allow_stale:
            return snapshot, config
        try:
            fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            fetched_dt = None
        if fetched_dt and datetime.utcnow() - fetched_dt < timedelta(minutes=settings.sync_interval_minutes):
            return snapshot, config

    return _refresh_snapshot(row, config), config


# ------------------------------------------------------------------ #
# Health / index
# ------------------------------------------------------------------ #
@app.get("/")
def index():
    return {
        "name": "Agile Comrade API",
        "version": "3.0.0",
        "status": "running",
        "endpoints": [
            "/api/health",
            "/api/config-defaults",
            "/api/profiles",
            "/api/profiles/{slug}",
            "/api/profiles/verify",
            "/api/risk-decision",
            "/api/test-config",
            "/api/snapshot",
            "/api/sync-now",
            "/api/generate-mitigations",
            "/api/next-sprint-risks",
            "/api/next-sprint-issues",
            "/api/generate-followup-message",
            "/api/stakeholder-report",
        ],
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "storage": "supabase" if store.enabled else "not-configured",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/config-defaults")
def config_defaults():
    defaults = UserConfig.from_defaults()
    return {
        "provider_options": ["gemini", "openrouter"],
        "default_models": {"gemini": settings.gemini_model, "openrouter": settings.openrouter_model},
        "defaults": {
            "jira_cloud_url": defaults.jira_cloud_url,
            "jira_projects": defaults.jira_projects,
            "llm_provider": defaults.llm_provider,
            "llm_model": defaults.llm_model,
        },
    }


# ------------------------------------------------------------------ #
# Profile CRUD
# ------------------------------------------------------------------ #
@app.post("/api/profiles")
def create_profile(request: Request, body: dict):
    body = body or {}
    slug = (body.get("slug") or "").strip().lower()
    access_token = (body.get("access_token") or "").strip() or secrets.token_urlsafe(32)

    if not _validate_slug(slug):
        return JSONResponse(
            {"status": "error", "error": "Slug must be 2-40 chars: lowercase letters, digits, hyphens"},
            status_code=400,
        )

    config = _config_from_body(body)
    if not config.jira_cloud_url or not config.jira_email or not config.jira_api_token:
        return JSONResponse(
            {"status": "error", "error": "jira_cloud_url, jira_email and jira_api_token are required"},
            status_code=400,
        )
    if not validate_jira_url(config.jira_cloud_url):
        return JSONResponse(
            {"status": "error", "error": "jira_cloud_url must be a https://<site>.atlassian.net URL"},
            status_code=400,
        )

    limited = rate_limit(f"create-profile:{_client_ip(request)}", max_requests=10, window_seconds=3600)
    if limited:
        return limited

    try:
        row = {
            "slug": slug,
            "access_token_hash": sha256_hex(access_token),
            "jira_cloud_url": config.jira_cloud_url,
            "jira_email": config.jira_email,
            "jira_api_token_enc": encrypt(config.jira_api_token),
            "project_keys": config.jira_projects,
            "llm_provider": config.llm_provider,
            "llm_model": config.llm_model,
            "llm_api_key_enc": encrypt(config.llm_api_key),
            "story_points_field": config.story_points_field or None,
        }
        created = store.create_profile(row)
    except DuplicateProfileError:
        return JSONResponse(
            {"status": "error", "error": f"Profile '{slug}' already exists"},
            status_code=409,
        )
    except RuntimeError as e:
        logger.error(f"Create profile failed: {e}")
        return JSONResponse(
            {"status": "error", "error": "Storage is not configured (check ENCRYPTION_KEY / SUPABASE_* on the server)"},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"Create profile failed unexpectedly: {e}")
        return JSONResponse({"status": "error", "error": "Storage error — please try again"}, status_code=500)

    return {
        "status": "created",
        "profile": _sanitized_config(created, config),
        "access_token": access_token,
        "message": "Store this access token; the backend only keeps a hash of it.",
    }


@app.post("/api/profiles/verify")
def verify_profile(request: Request, body: dict):
    body = body or {}
    slug = (body.get("slug") or "").strip().lower()
    access_token = (body.get("access_token") or "").strip()
    if not slug or not access_token:
        return JSONResponse({"status": "error", "error": "slug and access_token are required"}, status_code=400)

    limited = rate_limit(f"verify:{_client_ip(request)}", max_requests=10, window_seconds=300)
    if limited:
        return limited

    try:
        row = store.get_profile(urlquote(slug, safe=""))
    except Exception as e:
        logger.error(f"Verify lookup failed for {slug}: {e}")
        return JSONResponse({"status": "error", "error": "Storage unavailable"}, status_code=503)
    if not row or not hmac.compare_digest(row.get("access_token_hash", ""), sha256_hex(access_token)):
        return JSONResponse({"status": "error", "error": "Invalid slug or access token"}, status_code=401)

    config = UserConfig.from_row(row, decrypt)
    return {"status": "ok", "profile": _sanitized_config(row, config)}


@app.get("/api/profiles/{slug}")
def get_profile(slug: str, request: Request):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return JSONResponse({"status": "error", "error": "Profile mismatch"}, status_code=403)
    config = UserConfig.from_row(row, decrypt)
    return {"status": "ok", "profile": _sanitized_config(row, config)}


@app.put("/api/profiles/{slug}")
def update_profile(slug: str, request: Request, body: dict):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return JSONResponse({"status": "error", "error": "Profile mismatch"}, status_code=403)

    body = body or {}
    patch = {}

    for field, column in CONFIG_FIELD_MAP.items():
        if field not in body:
            continue
        value = body.get(field)
        if value is None:
            continue
        value = str(value).strip()
        if column.endswith("_enc"):
            if not value:
                continue  # blank => keep current secret
            patch[column] = encrypt(value)
        else:
            patch[column] = value or None

    if "jira_cloud_url" in patch and not validate_jira_url(patch["jira_cloud_url"]):
        return JSONResponse(
            {"status": "error", "error": "jira_cloud_url must be a https://<site>.atlassian.net URL"},
            status_code=400,
        )

    new_token = (body.get("access_token") or "").strip()
    if new_token:
        patch["access_token_hash"] = sha256_hex(new_token)

    if not patch:
        return JSONResponse({"status": "error", "error": "No fields to update"}, status_code=400)

    # Config changes invalidate the cached snapshot so the dashboard re-fetches
    # with the new project keys / credentials instead of serving stale data.
    if any(k in patch for k in ("jira_cloud_url", "jira_email", "jira_api_token_enc", "project_keys", "story_points_field")):
        store.clear_snapshot(slug)

    try:
        updated = store.update_profile(slug, patch)
    except RuntimeError as e:
        logger.error(f"Update profile failed: {e}")
        return JSONResponse(
            {"status": "error", "error": "Storage is not configured (check ENCRYPTION_KEY / SUPABASE_* on the server)"},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"Update profile failed unexpectedly: {e}")
        return JSONResponse({"status": "error", "error": "Storage error — please try again"}, status_code=500)

    config = UserConfig.from_row(updated or row, decrypt)
    return {
        "status": "ok",
        "profile": _sanitized_config(updated or row, config),
        "access_token": new_token or None,
    }


@app.delete("/api/profiles/{slug}")
def delete_profile(slug: str, request: Request):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return JSONResponse({"status": "error", "error": "Profile mismatch"}, status_code=403)
    store.delete_profile(slug)
    return {"status": "deleted", "slug": slug}


@app.post("/api/test-config")
def test_config(request: Request, body: dict):
    """Validate a config without storing it (Settings "Test Connection").

    Rate-limited and restricted to https://*.atlassian.net to prevent use as
    an unauthenticated SSRF relay / credential-stuffing target.
    """
    client_ip = request.client.host if request.client else "unknown"
    limited = rate_limit(f"test-config:{client_ip}", max_requests=10, window_seconds=300)
    if limited:
        return limited

    config = _config_from_body(body or {})
    if not config.jira_cloud_url or not config.jira_email or not config.jira_api_token:
        return JSONResponse(
            {"status": "error", "error": "jira_cloud_url, jira_email and jira_api_token are required"},
            status_code=400,
        )
    if not validate_jira_url(config.jira_cloud_url):
        return JSONResponse(
            {"status": "error", "error": "jira_cloud_url must be a https://<site>.atlassian.net URL"},
            status_code=400,
        )

    fetcher = JiraFetcher(config)
    result = fetcher.test_connection()

    # Scrub upstream response fragments before returning them to the client.
    for section in (result.get("auth"), *result.get("projects", {}).values()):
        if isinstance(section, dict) and not section.get("ok") and section.get("error"):
            section["error"] = _safe_upstream_error(section["error"])

    llm_check = {"provider": config.llm_provider, "model": config.llm_model, "ok": False}
    if not config.llm_api_key:
        llm_check["error"] = "No API key provided (optional for MVP)"
    else:
        try:
            agent = MitigationAgent(config)
            llm_check["ok"] = agent.model is not None
            if not llm_check["ok"]:
                llm_check["error"] = "Provider failed to initialize"
        except Exception as e:
            logger.error(f"LLM init failed during test-config: {e}")
            llm_check["error"] = "LLM provider could not be initialized"

    result["llm"] = llm_check
    auth_ok = result.get("auth", {}).get("ok", False)
    projects_ok = all(p.get("ok") for p in result.get("projects", {}).values())
    result["overall"] = {"ok": auth_ok and projects_ok}
    return {"status": "ok" if auth_ok and projects_ok else "partial", "result": result}


# ------------------------------------------------------------------ #
# Snapshot (single dashboard payload)
# ------------------------------------------------------------------ #
@app.get("/api/snapshot")
def get_snapshot(request: Request):
    row, error = _auth(request)
    if error:
        return error

    snapshot, _ = _get_or_refresh_snapshot(row)
    return snapshot


@app.post("/api/sync-now")
def sync_now(request: Request):
    row, error = _auth(request)
    if error:
        return error

    config = UserConfig.from_row(row, decrypt)
    snapshot = _refresh_snapshot(row, config)
    return {
        "status": "synced",
        "risks_found": len(snapshot.get("risks", [])),
        "last_sync": snapshot.get("last_sync"),
    }


@app.post("/api/profiles/{slug}/scope-baseline")
def set_scope_baseline(slug: str, request: Request, body: dict = None):
    """Declare the true planning commitment for an active sprint.

    Body: {"sprint_name": str, "total_sp": number}
    Overwrites the auto-captured baseline (marks it manual) so scope creep
    is measured against the declared value from the next sync onward. The
    per-issue map is refreshed from the current snapshot so future adds and
    estimate hikes keep being detected.
    """
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return JSONResponse({"status": "error", "error": "slug mismatch"}, status_code=403)

    sprint_name = (body or {}).get("sprint_name")
    total_sp = (body or {}).get("total_sp")
    if not sprint_name or total_sp is None or not isinstance(total_sp, (int, float)) or total_sp < 0:
        return JSONResponse(
            {"status": "error", "error": "sprint_name and non-negative total_sp are required"},
            status_code=400,
        )

    snapshot = _get_or_refresh_snapshot(row)[0]
    issues_by_sprint = {}
    for data in (snapshot.get("sprint_data") or {}).values():
        sprint = data.get("sprint") or {}
        if sprint.get("name"):
            issues_by_sprint[sprint["name"]] = data.get("issues", [])
    if sprint_name not in issues_by_sprint:
        return JSONResponse(
            {"status": "error", "error": f"No active sprint named '{sprint_name}' in the current snapshot"},
            status_code=404,
        )

    issue_map = {
        i.get("key"): (i.get("story_points", 0) or 0)
        for i in issues_by_sprint[sprint_name]
        if i.get("key")
    }
    prev_scope_meta = snapshot.get("scope_meta") or {"baselines": {}, "history": {}}
    baselines = prev_scope_meta.setdefault("baselines", {})
    previous = baselines.get(sprint_name)
    baselines[sprint_name] = {
        "total_sp": total_sp,
        "issues": issue_map,
        "captured_at": datetime.utcnow().isoformat(),
        "late_capture": False,
        "manual": True,
    }

    updated = {**snapshot, "scope_meta": prev_scope_meta}
    store.update_profile(slug, {"snapshot": updated})
    logger.info(
        f"📅 Manual scope baseline set for '{sprint_name}': {total_sp} SP "
        f"(was {previous.get('total_sp') if previous else 'none'})."
    )
    return {
        "status": "baseline-set",
        "sprint_name": sprint_name,
        "total_sp": total_sp,
        "previous_total_sp": previous.get("total_sp") if previous else None,
        "tracked_issues": len(issue_map),
        "manual": True,
    }


# ------------------------------------------------------------------ #
# AI / next-sprint endpoints (operate on the profile snapshot)
# ------------------------------------------------------------------ #
@app.post("/api/risk-decision")
def set_risk_decision(request: Request, body: dict = None):
    """Record the scrum master's human decision on a detected risk.

    Body: {"risk_id": str, "status": one of DECISION_STATUSES, "note": str?, "owner": str?}
    Merged into the snapshot's risk_decisions ledger (keyed by stable risk_id)
    and persisted so it survives the next sync/re-detection.
    """
    row, error = _auth(request)
    if error:
        return error

    body = body or {}
    risk_id = (body.get("risk_id") or "").strip()
    status = (body.get("status") or "").strip().lower()
    if not risk_id or status not in DECISION_STATUSES:
        return JSONResponse(
            {"status": "error", "error": f"risk_id and a valid status ({', '.join(DECISION_STATUSES)}) are required"},
            status_code=400,
        )

    snapshot, _ = _get_or_refresh_snapshot(row, allow_stale=True)
    if risk_id not in {r.get("risk_id") for r in snapshot.get("risks", [])}:
        return JSONResponse(
            {"status": "error", "error": "Risk not found in the current snapshot — it may have resolved or moved"},
            status_code=404,
        )

    decisions = dict(snapshot.get("risk_decisions") or {})
    decisions[risk_id] = {
        "status": status,
        "note": (body.get("note") or "").strip(),
        "owner": (body.get("owner") or "").strip(),
        "decided_at": datetime.utcnow().isoformat(),
    }

    updated = {**snapshot, "risk_decisions": decisions}
    for risk in updated.get("risks", []):
        if risk.get("risk_id") == risk_id:
            risk["decision"] = decisions[risk_id]
    store.update_profile(row["slug"], {"snapshot": updated})
    logger.info(f"🧑‍⚖️ Risk decision recorded | risk={risk_id} status={status} profile={row['slug']}")

    return {"status": "ok", "risk_id": risk_id, "decision": decisions[risk_id]}


@app.post("/api/generate-mitigations")
def generate_mitigations(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    t0 = time.time()
    snapshot, config = _get_or_refresh_snapshot(row, allow_stale=True)
    t_snap = time.time() - t0
    sprint_data = snapshot.get("sprint_data", {})
    lookup = {}
    for data in sprint_data.values():
        sprint = data.get("sprint")
        if not sprint:
            continue
        for issue in data.get("issues", []):
            lookup[issue.get("key")] = sprint.get("name")

    sprint_key_filter = (body or {}).get("sprint_key")

    sprints = {}
    for project_key, data in sprint_data.items():
        sprint = data.get("sprint")
        if not sprint:
            continue
        sprint_name = sprint.get("name")
        if sprint_key_filter and sprint_name != sprint_key_filter:
            continue
        if sprint_name not in sprints:
            sprints[sprint_name] = {
                "sprint_key": sprint_name,
                "project_key": project_key,
                "risks": [],
                "issues": data.get("issues", []),
            }

    for risk in snapshot.get("risks", []):
        sprint_key = risk.get("sprint_key") or lookup.get(risk.get("issue_key"))
        if not sprint_key:
            sprint_key = risk.get("issue_key", "Unknown")
        if sprint_key in sprints:
            sprints[sprint_key]["risks"].append(risk)

    agent = MitigationAgent(config)
    t_llm0 = time.time()
    mitigations = agent.generate_sprint_mitigation_plan(list(sprints.values()))
    logger.info(
        f"⏱️ generate_mitigations | snapshot={t_snap:.2f}s llm={time.time() - t_llm0:.2f}s "
        f"total={time.time() - t0:.2f}s sprints={len(sprints)}"
    )
    store.update_profile(row["slug"], {"snapshot": {**snapshot, "mitigations": mitigations}})

    return {
        "status": "generated",
        "mitigations": mitigations,
        "total": len(mitigations),
        "ai_used": all(m.get("ai_used", False) for m in mitigations) if mitigations else False,
        "llm": agent.get_model_info(),
    }


@app.post("/api/next-sprint-risks")
def next_sprint_risks(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    project_key = (body or {}).get("project_key")
    if not project_key:
        return JSONResponse({"status": "error", "error": "project_key is required"}, status_code=400)

    t0 = time.time()
    snapshot, config = _get_or_refresh_snapshot(row, allow_stale=True)
    t_snap = time.time() - t0
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return JSONResponse(
            {"status": "error", "error": f"No next sprint found for project {project_key}"},
            status_code=404,
        )

    issues = project_data.get("issues", [])
    risk_engine = RiskEngine()
    rule_based_risks = risk_engine.calculate_next_sprint_risks(issues)

    agent = MitigationAgent(config)
    t_llm0 = time.time()
    risks, ai_used, prompt, raw_response, ai_error = agent.analyze_next_sprint_risks(
        project_key=project_key,
        sprint=project_data.get("sprint", {}),
        issues=issues,
        rule_based_risks=rule_based_risks,
    )
    for r in risks:
        explain_risk(r)
    logger.info(
        f"⏱️ next_sprint_risks | snapshot={t_snap:.2f}s llm={time.time() - t_llm0:.2f}s "
        f"total={time.time() - t0:.2f}s project={project_key}"
    )

    return {
        "status": "analyzed",
        "project_key": project_key,
        "sprint_key": project_data["sprint"].get("name"),
        "risks": risks,
        "total": len(risks),
        "ai_used": ai_used,
        "error": ai_error,
        "prompt": prompt,
        "raw_response": raw_response,
        "llm": agent.get_model_info(),
    }


@app.post("/api/next-sprint-issues")
def next_sprint_issues(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    project_key = (body or {}).get("project_key")
    if not project_key:
        return JSONResponse({"status": "error", "error": "project_key is required"}, status_code=400)

    snapshot, config = _get_or_refresh_snapshot(row)
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return JSONResponse(
            {"status": "error", "error": f"No next sprint found for project {project_key}"},
            status_code=404,
        )

    issues = []
    for issue in project_data.get("issues", []):
        issues.append({
            "key": issue.get("key"),
            "summary": issue.get("summary"),
            "status": issue.get("status"),
            "assignee": issue.get("assignee", "Unassigned"),
            "story_points": issue.get("story_points", 0),
            "issue_type": issue.get("issue_type"),
            "due_date": issue.get("due_date"),
        })

    return {
        "status": "ok",
        "project_key": project_key,
        "sprint_key": project_data["sprint"].get("name"),
        "issues": issues,
        "total": len(issues),
    }


@app.post("/api/generate-followup-message")
def generate_followup_message(request: Request, body: dict = None):
    row, error = _auth(request)
    if error:
        return error

    issue_key = (body or {}).get("issue_key")
    if not issue_key:
        return JSONResponse({"status": "error", "error": "issue_key is required"}, status_code=400)

    t0 = time.time()
    config = UserConfig.from_row(row, decrypt)
    blocker_in = (body or {}).get("blocker")

    if blocker_in:
        # Fast path: the UI already has the risk object, so skip the full Jira
        # snapshot rebuild entirely (was the main cost of per-ticket drafts).
        blocker = dict(blocker_in)
        blocker.setdefault("issue_key", issue_key)
        agent = MitigationAgent(config)
        result = agent.generate_followup_message(blocker)
        result["issue_key"] = issue_key
        logger.info(f"⏱️ generate_followup_message | fast-path (no snapshot) llm={time.time() - t0:.2f}s issue={issue_key}")
        return result

    snapshot, _ = _get_or_refresh_snapshot(row)
    blocker = next(
        (r for r in snapshot.get("risks", []) if r.get("issue_key") == issue_key),
        {},
    )
    blocker.setdefault("issue_key", issue_key)

    agent = MitigationAgent(config)
    result = agent.generate_followup_message(blocker)
    result["issue_key"] = issue_key
    logger.info(f"⏱️ generate_followup_message | snapshot-rebuild llm={time.time() - t0:.2f}s issue={issue_key}")
    return result


@app.get("/api/stakeholder-report")
def stakeholder_report(request: Request):
    row, error = _auth(request)
    if error:
        return error

    snapshot, config = _get_or_refresh_snapshot(row)
    agent = MitigationAgent(config)
    report = agent.generate_stakeholder_report(
        snapshot.get("risks", []),
        snapshot.get("mitigations", []),
    )
    return {"report": report, "generated_at": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.port, reload=settings.debug)