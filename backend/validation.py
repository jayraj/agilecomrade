"""Input validation and upstream-error scrubbing shared across the API."""

import re

# Profile slug: lowercase, starts alphanumeric, 2-40 chars.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

# Only https://<site>.atlassian.net URLs are accepted (blocks SSRF to internal hosts).
JIRA_URL_RE = re.compile(r"^https://[a-z0-9][a-z0-9-]*\.atlassian\.net$")


def validate_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or ""))


def validate_jira_url(url: str) -> bool:
    u = (url or "").strip().rstrip("/").lower()
    return bool(JIRA_URL_RE.match(u))


def safe_upstream_error(detail: str) -> str:
    """Map upstream error fragments to safe client-facing messages."""
    d = (detail or "").lower()
    if "401" in d or "unauthorized" in d or "403" in d or "forbidden" in d:
        return "Authentication failed — check email / API token"
    if "404" in d or "not found" in d:
        return "Resource not found — check the URL and project keys"
    if "timed out" in d or "timeout" in d:
        return "Connection timed out"
    return "Request failed — verify the configuration"
