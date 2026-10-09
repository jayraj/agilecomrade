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

-- ---------------------------------------------------------------------------
-- Shared state (kv_store): rate-limit counters, refresh locks, TTL caches.
-- One generic table, namespaced by `bucket` (rate | lock | llm_cache | tz_cache).
-- Backend-only like profiles: RLS on, no anon policies (service role bypasses).
-- Enable the shared backend with SRR_STATE_BACKEND=supabase.
-- ---------------------------------------------------------------------------

create table if not exists public.kv_store (
  bucket        text not null,
  key           text not null,
  value         jsonb null,
  count         integer not null default 0,
  window_start  timestamptz null,
  locked_until  timestamptz null,
  expires_at    timestamptz null,
  updated_at    timestamptz not null default now(),
  primary key (bucket, key)
);

alter table public.kv_store enable row level security;

-- Fixed-window increment: returns the count within the current window,
-- resetting once the window has elapsed. Atomic through the row-level upsert.
create or replace function public.kv_incr(p_bucket text, p_key text, p_window_seconds int)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_now   timestamptz := now();
  v_count int;
begin
  insert into public.kv_store (bucket, key, count, window_start, expires_at, updated_at)
  values (p_bucket, p_key, 1, v_now, v_now + make_interval(secs => p_window_seconds), v_now)
  on conflict (bucket, key) do update
    set count = case
          when kv_store.window_start is null
               or kv_store.window_start + make_interval(secs => p_window_seconds) <= v_now
          then 1
          else kv_store.count + 1
        end,
        window_start = case
          when kv_store.window_start is null
               or kv_store.window_start + make_interval(secs => p_window_seconds) <= v_now
          then v_now
          else kv_store.window_start
        end,
        expires_at = v_now + make_interval(secs => p_window_seconds),
        updated_at = v_now
  returning kv_store.count into v_count;
  return v_count;
end;
$$;

-- Acquire a named lock, honouring an existing unexpired lease. Returns true
-- only when this call took (or renewed) the lock; a crashed holder's lock
-- auto-expires after p_ttl_seconds.
create or replace function public.kv_acquire_lock(p_bucket text, p_key text, p_ttl_seconds int)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_now      timestamptz := now();
  v_acquired boolean;
begin
  insert into public.kv_store (bucket, key, locked_until, expires_at, updated_at)
  values (p_bucket, p_key, v_now + make_interval(secs => p_ttl_seconds),
          v_now + make_interval(secs => p_ttl_seconds), v_now)
  on conflict (bucket, key) do update
    set locked_until = v_now + make_interval(secs => p_ttl_seconds),
        expires_at = v_now + make_interval(secs => p_ttl_seconds),
        updated_at = v_now
    where kv_store.locked_until is null or kv_store.locked_until <= v_now
  returning true into v_acquired;
  return coalesce(v_acquired, false);
end;
$$;

-- Release a named lock early (TTL remains the crash-safety backstop).
create or replace function public.kv_release_lock(p_bucket text, p_key text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.kv_store
     set locked_until = null, updated_at = now()
   where bucket = p_bucket and key = p_key;
end;
$$;

-- RPCs are backend-only: revoke the default PUBLIC execute and grant service_role.
revoke all on function public.kv_incr(text, text, int) from public;
revoke all on function public.kv_acquire_lock(text, text, int) from public;
revoke all on function public.kv_release_lock(text, text) from public;
grant execute on function public.kv_incr(text, text, int) to service_role;
grant execute on function public.kv_acquire_lock(text, text, int) to service_role;
grant execute on function public.kv_release_lock(text, text) to service_role;