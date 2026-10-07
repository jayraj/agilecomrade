"""Thin PostgREST client for the Supabase `profiles` table (service-role only)."""

import logging
from urllib.parse import quote as urlquote

import requests

from config import settings

logger = logging.getLogger(__name__)

TABLE = "profiles"


class DuplicateProfileError(RuntimeError):
    """Raised when a profile with the same slug already exists."""


class SupabaseStore:
    def __init__(self, url: str = "", service_role_key: str = ""):
        self.base_url = (url or settings.supabase_url).rstrip("/")
        self.service_role_key = service_role_key or settings.supabase_service_role_key

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.service_role_key)

    def _headers(self, prefer: str = "return=representation") -> dict:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Prefer": prefer,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs):
        if not self.enabled:
            raise RuntimeError("Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing)")
        url = f"{self.base_url}/rest/v1/{path}"
        headers = self._headers()
        # Bodyless verbs (DELETE above all) must not advertise a JSON payload:
        # a Content-Type of application/json with an empty body is rejected by
        # some PostgREST versions as PGRST123 "Empty or invalid json".
        has_payload = kwargs.get("json") is not None or kwargs.get("data") not in (None, "", b"")
        if not has_payload:
            headers.pop("Content-Type", None)
        kwargs.setdefault("headers", headers)
        kwargs["timeout"] = 30
        response = requests.request(method, url, **kwargs)
        if response.status_code >= 400:
            if response.status_code == 409:
                raise DuplicateProfileError("Profile already exists")
            raise RuntimeError(f"Supabase {method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.text else []

    def get_profile(self, slug: str) -> dict | None:
        rows = self._request("GET", f"{TABLE}?slug=eq.{urlquote(slug, safe='')}&select=*")
        return rows[0] if rows else None

    def create_profile(self, row: dict) -> dict:
        rows = self._request("POST", TABLE, json=row)
        return rows[0] if rows else row

    def update_profile(self, slug: str, patch: dict) -> dict | None:
        patch = {k: v for k, v in patch.items() if v is not None}
        rows = self._request("PATCH", f"{TABLE}?slug=eq.{urlquote(slug, safe='')}", json=patch)
        return rows[0] if rows else None

    def clear_snapshot(self, slug: str):
        """Invalidate the cached snapshot so the next request re-fetches."""
        rows = self._request(
            "PATCH",
            f"{TABLE}?slug=eq.{urlquote(slug, safe='')}",
            json={"snapshot": None, "fetched_at": None},
        )
        return rows[0] if rows else None

    def delete_profile(self, slug: str) -> list[dict]:
        """Delete a profile row and return the rows PostgREST actually removed.

        `Prefer: return=representation` makes PostgREST echo the deleted rows,
        so an empty list means the filter matched nothing (row already gone,
        tenant mismatch, RLS filter) — the caller must not report that as a
        successful delete.
        """
        return self._request("DELETE", f"{TABLE}?slug=eq.{urlquote(slug, safe='')}")
