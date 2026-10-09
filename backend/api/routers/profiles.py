"""Profile CRUD and connection testing."""

import hmac
import logging
import secrets
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Request

from api.deps import _auth, _client_ip, _error, rate_limit
from config import UserConfig
from crypto import decrypt_strict, encrypt, sha256_hex
from jira_fetcher import JiraFetcher
from mitigation_agent import MitigationAgent
from services import store
from services.profile_service import CONFIG_FIELD_MAP, config_from_body, sanitized_config
from supabase_store import DuplicateProfileError
from validation import safe_upstream_error, validate_jira_url, validate_slug

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/profiles")
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
        # Don't echo the slug: creation responses should not confirm which
        # slugs exist (keeps this consistent with verify's generic 401).
        return _error("Profile already exists or slug is unavailable", 409)
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


@router.post("/api/profiles/verify")
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


@router.get("/api/profiles/{slug}")
def get_profile(slug: str, request: Request):
    row, error = _auth(request)
    if error:
        return error
    if row.get("slug") != slug:
        return _error("Profile mismatch", 403)
    config = UserConfig.from_row(row, decrypt_strict)
    return {"status": "ok", "profile": sanitized_config(row, config)}


@router.put("/api/profiles/{slug}")
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


@router.delete("/api/profiles/{slug}")
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


@router.post("/api/test-config")
def test_config(request: Request, body: dict):
    """Validate a config without storing it (Settings "Test Connection").

    Rate-limited and restricted to https://*.atlassian.net to prevent use as
    an unauthenticated SSRF relay / credential-stuffing target.
    """
    limited = rate_limit(f"test-config:{_client_ip(request)}", max_requests=10, window_seconds=300)
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
