"""Profile <-> UserConfig mapping and safe serialization."""

from config import UserConfig

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


def config_from_body(body: dict) -> UserConfig:
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


def sanitized_config(row: dict, config: UserConfig) -> dict:
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
