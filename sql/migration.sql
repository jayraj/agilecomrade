-- Sprint Risk Radar v2 - multi-tenant profiles
-- Run this in the Supabase SQL Editor.
--
-- One row per scrum master / client profile. API keys are stored encrypted
-- (Fernet: AES-128-CBC + HMAC-SHA256) and the access token only as a SHA-256
-- hash. The backend owns all reads/writes using the service-role key, so
-- there are NO anon policies.

create table if not exists public.profiles (
  slug                  text primary key,
  access_token_hash     text not null,
  jira_cloud_url        text not null,
  jira_email            text not null,
  jira_api_token_enc    text not null default '',
  project_keys          text not null default '',
  llm_provider          text not null default 'gemini',
  llm_model             text not null default '',
  llm_api_key_enc       text not null default '',
  story_points_field    text null,
  snapshot              jsonb null,
  burndown_history      jsonb null,
  fetched_at            timestamptz null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- No anon/authenticated policies on purpose: the backend uses the
-- service-role key, which bypasses RLS. Do not add policies here unless you
-- later introduce Supabase Auth and want direct client access.