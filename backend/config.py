from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # Supabase (storage for profiles). The backend owns all access via the
    # service-role key; the frontend never talks to Supabase directly.
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Encryption key for API keys at rest (AES-GCM via Fernet). Generate one
    # with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # Comma-separated allowed CORS origins (the deployed frontend URL).
    cors_origins: str = "http://localhost:3001,http://127.0.0.1:3001"

    # Defaults used when a user has no profile row yet (landing/config-defaults).
    jira_cloud_url: str = "https://your-domain.atlassian.net"
    jira_email: str = ""
    jira_api_token: str = ""
    jira_projects: str = ""

    # LLM defaults (provider dropdown offers gemini | openrouter).
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"

    # Risk thresholds (v1 triggers — unchanged; v2 replaces only scoring)
    story_update_threshold_hours: int = 24
    burndown_behind_threshold: float = 10.0  # gap %
    burndown_grace_period_fraction: float = 0.25
    qa_bottleneck_threshold: int = 2  # stories

    # v2 rubric constants
    time_pressure_table: tuple[tuple[float, float], ...] = (
        (0.25, 0.6),
        (0.50, 0.8),
        (0.75, 1.1),
        (0.90, 1.4),
        (1.01, 1.7),
    )
    workflow_stage_weights: dict[str, float] = {
        "To Do": 0.6,
        "In Progress": 0.9,
        "Code Review": 1.1,
        "In QA Review": 1.3,
        "QA Review": 1.3,
    }
    # Status -> completion fraction for weighted burndown SP (v1 semantics, kept)
    status_completion_weights: dict[str, float] = {
        "Done": 1.0,
        "In Review": 0.75,
        "Code Review": 0.75,
        "QA Review": 0.8,
        "In QA Review": 0.8,
    }
    blocked_stage_weight: float = 1.4
    default_stage_weight: float = 1.0
    size_weight_min: float = 0.4
    size_weight_max: float = 1.6
    # Defect quality-risk tiering (scored through the P x I matrix; tier sets
    # impact, open/fixed/escaped sets probability — see risk_matrix.bug_p/bug_i).
    bug_priority_tiers: dict[str, str] = {
        "Highest": "P1",
        "High": "P2",
        "Medium": "P3",
        "Low": "P4",
        "Lowest": "P4",
    }
    bug_default_tier: str = "P3"  # unknown/missing priority
    bug_prod_escape_labels: list[str] = ["production", "prod-escape"]
    qa_throughput_default: float = 1.0  # tickets/day when no history
    qa_throughput_window: int = 3  # rolling sprints
    no_progress_grace_days: int = 2
    trend_flat: float = 1.3
    trend_slow: float = 1.0
    trend_fast: float = 0.7
    fan_out_factor: float = 1.3
    assignee_no_active_factor: float = 1.4
    # Assignee overload: flag when one person holds at least this many open
    # items AND at least `overload_ratio` x the team's average open-item count.
    # Both gates are required so a small balanced team never fires.
    overload_min_items: int = 3
    overload_ratio: float = 1.5
    burndown_history_size: int = 8
    # Scope-creep detection (baseline captured on first active-sprint sync)
    scope_creep_min_growth_pct: float = 10.0
    scope_creep_cap: float = 85.0
    # Product rule: ANY confirmed scope creep is a red flag, regardless of
    # how small — floor the displayed score at CRITICAL territory.
    scope_creep_floor_score: int = 80
    scope_history_size: int = 8
    scope_baseline_grace_hours: int = 24  # later capture => lower confidence

    # Server / scheduler (scheduler removed for serverless)
    port: int = 5002
    debug: bool = False
    sync_interval_minutes: int = 5
    jira_field_mapping: dict[str, str] = {
        "story_points": "customfield_10102",
    }

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()


class UserConfig(BaseModel):
    """Per-profile configuration supplied by the browser (or defaults).

    Plaintext in memory / transit; encrypted at rest in Supabase.
    """

    jira_cloud_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_projects: str = ""
    llm_provider: str = "gemini"
    llm_model: str = ""
    llm_api_key: str = ""
    story_points_field: str = ""

    @classmethod
    def from_defaults(cls) -> "UserConfig":
        return cls(
            jira_cloud_url=settings.jira_cloud_url,
            jira_email=settings.jira_email,
            jira_api_token=settings.jira_api_token,
            jira_projects=settings.jira_projects,
            llm_provider=settings.llm_provider,
            llm_model=settings.gemini_model if settings.llm_provider == "gemini" else settings.openrouter_model,
            llm_api_key=(
                settings.gemini_api_key
                if settings.llm_provider == "gemini"
                else settings.openrouter_api_key
            ),
        )

    @classmethod
    def from_row(cls, row: dict, decrypt) -> "UserConfig":
        """Rehydrate from a stored profile row, decrypting API keys."""
        if not row:
            return cls.from_defaults()
        return cls(
            jira_cloud_url=row.get("jira_cloud_url", ""),
            jira_email=row.get("jira_email", ""),
            jira_api_token=decrypt(row.get("jira_api_token_enc", "")),
            jira_projects=row.get("project_keys", ""),
            llm_provider=row.get("llm_provider", "gemini"),
            llm_model=row.get("llm_model", ""),
            llm_api_key=decrypt(row.get("llm_api_key_enc", "")),
            story_points_field=row.get("story_points_field") or "",
        )

    @property
    def project_list(self) -> list[str]:
        return [p.strip() for p in self.jira_projects.split(",") if p.strip()]