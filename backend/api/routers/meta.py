"""Service metadata: index, health, and configuration defaults."""

from fastapi import APIRouter

from config import UserConfig, settings
from risk_components import now_utc
from services import store

router = APIRouter()


@router.get("/")
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


@router.get("/api/health")
def health():
    return {
        "status": "healthy",
        "storage": "supabase" if store.enabled else "not-configured",
        "timestamp": now_utc().isoformat(),
    }


@router.get("/api/config-defaults")
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
