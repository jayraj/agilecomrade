import hmac
import logging
import os
import secrets
import time
from urllib.parse import quote as urlquote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.deps import (
    _MAX_BODY_BYTES,
    _RATE_BUCKETS,
    _RATE_MAX_KEYS,
    _auth,
    _client_ip,
    _error,
    rate_limit,
    store,
)
from config import UserConfig, settings
from crypto import DecryptionError, decrypt_strict, encrypt, sha256_hex
from jira_fetcher import JiraFetcher
from mitigation_agent import MitigationAgent
from risk_components import now_utc
from risk_engine import RiskEngine
from risk_explainer import DECISION_STATUSES, explain_risk
from services.profile_service import CONFIG_FIELD_MAP, config_from_body, sanitized_config
from services.snapshot_service import (
    _get_or_refresh_snapshot,
    _refresh_lock,
    _refresh_snapshot,
    _refresh_snapshot_locked,
)
from supabase_store import DuplicateProfileError
from validation import safe_upstream_error, validate_slug

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


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
        return _error("Request body too large", 413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    # Authenticated payloads must not be cached by shared/intermediary caches.
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return _error("Internal server error", 500)


@app.exception_handler(DecryptionError)
async def decryption_error_handler(_request: Request, _exc: DecryptionError) -> JSONResponse:
    logger.error("Stored credentials could not be decrypted")
    return _error(
        "Stored credentials can't be decrypted — reconnect this profile in Settings (re-enter the Jira API token).",
        409,
    )


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
        ],
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "storage": "supabase" if store.enabled else "not-configured",
        "timestamp": now_utc().isoformat(),
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

    if not validate_slug(slug):
        return _error("Slug must be 2-40 chars: lowercase letters, digits, hyphens", 400)

    config = config_from_body(body)
    if not config.jira_cloud_url or not config.jira_email or not config.jira_api_token:
        return _error("jira_cloud_url, jira_email and jira_api_token are required", 400)
    if not validate_jira_url(config.jira_cloud_url):
        return _error("jira_cloud_url must be a https://<site>.atlassian.net URL", 400)

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
        return _error(f"Profile '{slug}' already exists", 409)
    except RuntimeError as e:
        logger.error(f"Create profile failed: {e}")
        return _error("Storage is not configured (check ENCRYPTION_KEY / SUPABASE_* on the server)", 500)
    except Exception as e:
        logger.error(f"Create profile failed unexpectedly: {e}")
        return _error("Storage error — please try again", 500)

    return {
        "status": "created",
        "profile": sanitized_config(created, config),
        "access_token": access_token,
        "message": "Store this access token; the backend only keeps a hash of it.",
    }


@app.post("/api/profiles/verify")
def verify_profile(request: Request, body: dict):
    body = body or {}
    slug = (body.get("slug") or "").strip().lower()
    access_token = (body.get("access_token") or "").strip()
    if not slug or not access_token:
        return _error("slug and access_token are required", 400)

    limited = rate_limit(f"verify:{_client_ip(request)}", max_requests=10, window_seconds=300)
    if limited:
        return limited

    try:
        row = store.get_profile(urlquote(slug, safe=""))
    except Exception as e:
        logger.error(f"Verify lookup failed for {slug}: {e}")
        return _error("Storage unavailable", 503)
    if not row or not hmac.compare_digest(row.get("access_token_hash", ""), sha256_hex(access_token)):
        return _error("Invalid slug or access token", 401)

    config = UserConfig.from_row(row, decrypt_strict)
    return {"status": "ok", "profile": sanitized_config(row, config)}


@app.get("/api/profiles/{slug}")
def get_profile(slug: str, request: Request):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return _error("Profile mismatch", 403)
    config = UserConfig.from_row(row, decrypt_strict)
    return {"status": "ok", "profile": sanitized_config(row, config)}


@app.put("/api/profiles/{slug}")
def update_profile(slug: str, request: Request, body: dict):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return _error("Profile mismatch", 403)

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
        return _error("jira_cloud_url must be a https://<site>.atlassian.net URL", 400)

    new_token = (body.get("access_token") or "").strip()
    if new_token:
        patch["access_token_hash"] = sha256_hex(new_token)

    if not patch:
        return _error("No fields to update", 400)

    # Config changes invalidate the cached snapshot so the dashboard re-fetches
    # with the new project keys / credentials instead of serving stale data.
    if any(k in patch for k in ("jira_cloud_url", "jira_email", "jira_api_token_enc", "project_keys", "story_points_field")):
        store.clear_snapshot(slug)

    try:
        updated = store.update_profile(slug, patch)
    except RuntimeError as e:
        logger.error(f"Update profile failed: {e}")
        return _error("Storage is not configured (check ENCRYPTION_KEY / SUPABASE_* on the server)", 500)
    except Exception as e:
        logger.error(f"Update profile failed unexpectedly: {e}")
        return _error("Storage error — please try again", 500)

    config = UserConfig.from_row(updated or row, decrypt_strict)
    return {
        "status": "ok",
        "profile": sanitized_config(updated or row, config),
        "access_token": new_token or None,
    }


@app.delete("/api/profiles/{slug}")
def delete_profile(slug: str, request: Request):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return _error("Profile mismatch", 403)
    try:
        deleted = store.delete_profile(slug)
    except RuntimeError as e:
        logger.error(f"Delete profile failed: {e}")
        return _error("Storage is not configured (check ENCRYPTION_KEY / SUPABASE_* on the server)", 500)
    except Exception as e:
        logger.error(f"Delete profile failed unexpectedly: {e}")
        return _error("Storage error — please try again", 500)
    if not deleted:
        # PostgREST echoed no rows back, so nothing matched the filter: the
        # row was already gone (or never existed). Don't claim a fresh delete.
        return _error(f"Profile '{slug}' not found", 404)
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

    config = config_from_body(body or {})
    if not config.jira_cloud_url or not config.jira_email or not config.jira_api_token:
        return _error("jira_cloud_url, jira_email and jira_api_token are required", 400)
    if not validate_jira_url(config.jira_cloud_url):
        return _error("jira_cloud_url must be a https://<site>.atlassian.net URL", 400)

    fetcher = JiraFetcher(config)
    result = fetcher.test_connection()

    # Scrub upstream response fragments before returning them to the client.
    for section in (result.get("auth"), *result.get("projects", {}).values()):
        if isinstance(section, dict) and not section.get("ok") and section.get("error"):
            section["error"] = safe_upstream_error(section["error"])

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

    config = UserConfig.from_row(row, decrypt_strict)
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
        return _error("slug mismatch", 403)

    sprint_name = (body or {}).get("sprint_name")
    total_sp = (body or {}).get("total_sp")
    if not sprint_name or total_sp is None or not isinstance(total_sp, (int, float)) or total_sp < 0:
        return _error("sprint_name and non-negative total_sp are required", 400)

    snapshot = _get_or_refresh_snapshot(row)[0]
    issues_by_sprint = {}
    for data in (snapshot.get("sprint_data") or {}).values():
        sprint = data.get("sprint") or {}
        if sprint.get("name"):
            issues_by_sprint[sprint["name"]] = data.get("issues", [])
    if sprint_name not in issues_by_sprint:
        return _error(f"No active sprint named '{sprint_name}' in the current snapshot", 404)

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
        "captured_at": now_utc().isoformat(),
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
        return _error(f"risk_id and a valid status ({', '.join(DECISION_STATUSES)}) are required", 400)

    snapshot, _ = _get_or_refresh_snapshot(row, allow_stale=True)
    if risk_id not in {r.get("risk_id") for r in snapshot.get("risks", [])}:
        return _error("Risk not found in the current snapshot — it may have resolved or moved", 404)

    decisions = dict(snapshot.get("risk_decisions") or {})
    decisions[risk_id] = {
        "status": status,
        "note": (body.get("note") or "").strip(),
        "owner": (body.get("owner") or "").strip(),
        "decided_at": now_utc().isoformat(),
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
        return _error("project_key is required", 400)

    t0 = time.time()
    snapshot, config = _get_or_refresh_snapshot(row, allow_stale=True)
    t_snap = time.time() - t0
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return _error(f"No next sprint found for project {project_key}", 404)

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
        return _error("project_key is required", 400)

    snapshot, config = _get_or_refresh_snapshot(row)
    project_data = snapshot.get("next_sprint_data", {}).get(project_key)
    if not project_data or not project_data.get("sprint"):
        return _error(f"No next sprint found for project {project_key}", 404)

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
        return _error("issue_key is required", 400)

    t0 = time.time()
    config = UserConfig.from_row(row, decrypt_strict)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.port, reload=settings.debug)