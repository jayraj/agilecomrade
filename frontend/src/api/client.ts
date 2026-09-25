import axios from 'axios'
import { profileApi } from './config'

export const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? 'http://127.0.0.1:5002' : '/api')

/** Debug visibility for the "🔍 View AI Prompt & Raw Response" sections. Opt-in via VITE_SHOW_AI_DEBUG=true. */
export const SHOW_AI_DEBUG = import.meta.env.VITE_SHOW_AI_DEBUG === 'true'

/** Feedback form URL; set VITE_FEEDBACK_URL to show the footer Feedback link. */
export const FEEDBACK_URL = import.meta.env.VITE_FEEDBACK_URL || ''

/** Extracts the backend's error message from an axios error, with a fallback. */
export const apiErrorMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { error?: string } | undefined
    if (data?.error) return data.error
    return error.message || `Request failed with status code ${error.response?.status ?? 500}`
  }
  return error instanceof Error ? error.message : 'Request failed'
}

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 90000,
})

// Attach the active profile's slug + token to every request.
api.interceptors.request.use((config) => {
  const active = profileApi.active()
  if (active) {
    config.headers.set('X-SRR-Profile', active.slug)
    config.headers.set('X-SRR-Token', active.token)
  }
  return config
})

export interface HealthInfo {
  status: string
  storage: string
  timestamp: string
}

export interface RiskDetail {
  key: string
  summary: string
  status: string
  story_points: number
  assignee: string
  due_date?: string
}

export interface RadarCard {
  issue_key: string
  sprint_key: string
  project_key: string
  start_date?: string
  end_date?: string
  risk_type: string
  risk_types?: string[]
  risk_score: number
  raw_score?: number
  severity: string
  summary: string
  assignee?: string
  issue_count?: number
  total_sp: number
  completed_sp: number
  burndown_gap_percent?: number | null
  details: RiskDetail[]
}

export interface RiskSignalFact {
  label: string
  value: string
}

export interface RiskSignal {
  label: string
  facts: RiskSignalFact[]
}

export type RiskDecisionStatus =
  | 'pending'
  | 'accepted'
  | 'monitoring'
  | 'mitigating'
  | 'escalated'
  | 'dismissed'

export interface RiskDecision {
  status: RiskDecisionStatus
  note?: string
  owner?: string
  decided_at?: string
  /** Auto-set when a decided risk is no longer detected (ledger bookkeeping). */
  outcome?: string
  resolved_at?: string
  last_seen?: string
}

export interface Blocker {
  type: string
  sprint_key?: string
  severity?: string
  summary?: string
  issue_key?: string
  assignee?: string
  due_date?: string
  hours_since_update?: number
  days_since_created?: number
  priority?: string
  burndown_gap_percent?: number
  completed_sp?: number
  total_sp?: number
  risk_score?: number
  /* 5x5 probability x impact matrix fields (sprint-level risks, see backend
   * risk_matrix.py). Present when the risk was scored with the matrix; absent
   * on ticket-level risks that still use the legacy product model. `matrix_value`
   * is the 1-25 P x I product that drives the 0-100 risk_score. */
  probability?: number
  impact?: number
  matrix_value?: number
  count?: number
  issue_keys?: string[]
  recommendation?: string
  details?: unknown[]
  days_remaining?: number
  backlog_clear_days?: number
  qa_stories_count?: number
  stalled_issues?: { key: string; summary?: string; assignee?: string; hours_since_update: number }[]
  overdue_issues?: { key: string; summary?: string; days_overdue: number }[]
  /** Transparency fields (signal → cause → action → human decision). */
  risk_id?: string
  signal?: RiskSignal
  suspected_cause?: string
  suggested_action?: string
  decision?: RiskDecision
  /** Human-readable "why is this risk critical/medium/low?" (score-driver math). */
  severity_reason?: string
  /** Score-math transparency: band + driver chips the backend explainer latched
   *  (`attach_factors` in backend/risk_explainer.py). For matrix-scored risks
   *  `band` holds the "P5 × I4 = 20 → SEVERITY (range)" chip plus a named-scale
   *  chip; for legacy risks it holds the "SEVERITY (range) (raw X → score Y)"
   *  chip and `drivers` the exact {icon,label} multipliers. Additive: absent on
   *  legacy payloads, so all reads defensively fall back to severity_reason. */
  factors?: {
    band?: string[]
    drivers?: { icon?: string; label?: string }[]
  } | null
}

export interface Mitigation {
  sprint_key: string
  project_key?: string
  risk_types?: string[]
  risk_count?: number
  burndown_gap_percent?: number
  action_items?: string[]
  owner?: string
  timeline?: string
  success_criteria?: string[]
  confidence?: number
  ai_used?: boolean
  fallback_reason?: string
  error?: string
  prompt?: string
  raw_response?: string
  llm?: { provider?: string; model?: string }
}

export interface MitigationResponse {
  mitigations: Mitigation[]
  ai_used: boolean
  llm?: { provider?: string; model?: string }
}

export interface NextSprintProject {
  project_key: string
  sprint_key: string
  start_date?: string
  end_date?: string
  total_sp: number
  issue_count: number
  issue_types: Record<string, number>
  risk_score?: number
  at_risk_count?: number
}

export interface NextSprintIssue {
  key: string
  summary: string
  status: string
  assignee: string
  story_points: number
  issue_type?: string
  due_date?: string
}

export interface NextSprintAnalysis {
  risks: Blocker[]
  ai_used: boolean
  prompt?: string
  raw_response?: string
  error?: string
  llm?: { provider?: string; model?: string }
}

export interface SprintOverviewProject {
  project_key: string
  sprint_name: string
  start_date?: string
  end_date?: string
  total_sp: number
  completed_sp: number
  in_progress_sp: number
  remaining_sp: number
  completion_percent: number
  issue_count?: number
}

export interface SprintOverviewResponse {
  projects: SprintOverviewProject[]
  total_sp: number
  completed_sp: number
  in_progress_sp: number
  total_issue_count?: number
}

export interface VelocitySprint {
  sprint_key: string
  total_sp: number
  completed_sp: number
  completed_percent: number
}

export type VelocityData = Record<string, VelocitySprint[]>

export interface DeliveryHealthProject {
  project_key: string
  sprint_key: string
  status: string
  start_date?: string
  planned_end_date?: string
  total_sp: number
  completed_sp: number
  completion_percent: number
  burndown_gap_percent: number
  avg_velocity?: number | null
  rag: string
  why?: string
  forecast_date?: string
  forecast_delay_days: number
  timeline_risk_score: number
}

export interface RiskSummary {
  total_risks: number
  high_severity: number
  medium_severity: number
  overall_sprint_health: number
}

// Single snapshot payload served by GET /api/snapshot.
export interface Snapshot {
  radar_data: RadarCard[]
  blockers: Blocker[]
  sprint_overview: SprintOverviewResponse
  next_sprint_overview: { projects: NextSprintProject[]; total_sp: number; issue_count: number }
  velocity: VelocityData
  delivery_health: DeliveryHealthProject[]
  risks: Blocker[]
  total_risks: number
  summary: RiskSummary
  mitigations: Mitigation[]
  burndown_history: Record<string, number[]>
  scope_meta?: {
    baselines: Record<string, { total_sp: number; captured_at: string; manual?: boolean }>
    history: Record<string, number[]>
  }
  /** Human risk decisions persisted across syncs, keyed by stable risk_id. */
  risk_decisions?: Record<string, RiskDecision>
  /** Jira user's profile timezone (e.g. "Asia/Kolkata") — the source of truth
   * for all calendar-day math so dates match the Jira UI. */
  jira_timezone?: string
  sprint_data?: Record<
    string,
    {
      sprint?: { name?: string }
      issues?: Array<{ key?: string; summary?: string; assignee?: string; story_points?: number; status?: string }>
    }
  >
  last_sync: string | null
}

export interface FollowupMessage {
  issue_key: string
  message?: string
  generated_by?: string
  fallback_reason?: string
}

export interface ProfileConfig {
  slug: string
  jira_cloud_url: string
  jira_email: string
  jira_projects: string
  llm_provider: string
  llm_model: string
  story_points_field?: string
  fetched_at?: string | null
}

export interface TestConfigResult {
  overall?: { ok: boolean }
  auth?: { ok: boolean; error?: string }
  projects?: Record<string, { ok: boolean; error?: string; active_sprint?: string | null }>
  story_points_field?: { ok: boolean; error?: string }
  llm?: { provider?: string; model?: string; ok?: boolean; error?: string }
}

// ------------------------------------------------------------------ //
// Unauthenticated endpoints
// ------------------------------------------------------------------ //
export const apiConfigDefaults = async (): Promise<{
  provider_options: string[]
  default_models: Record<string, string>
  defaults: { jira_cloud_url: string; jira_projects: string; llm_provider: string; llm_model: string }
}> => (await api.get('/api/config-defaults')).data

export const apiCreateProfile = async (body: Record<string, unknown>): Promise<{
  status: string
  profile: ProfileConfig
  access_token: string
}> => (await api.post('/api/profiles', body)).data

export const apiTestConfig = async (body: Record<string, unknown>): Promise<{
  status: string
  result: TestConfigResult
}> => (await api.post('/api/test-config', body)).data

// ------------------------------------------------------------------ //
// Profile-gated (auth headers attached by interceptor)
// ------------------------------------------------------------------ //
export const apiSnapshot = async (): Promise<Snapshot> => (await api.get('/api/snapshot')).data
export const apiSyncNow = async (): Promise<{ status: string; risks_found: number; last_sync: string }> =>
  (await api.post('/api/sync-now')).data

export const apiGetProfile = async (slug: string): Promise<{ status: string; profile: ProfileConfig }> =>
  (await api.get(`/api/profiles/${slug}`)).data

export const apiUpdateProfile = async (
  slug: string,
  body: Record<string, unknown>,
): Promise<{ status: string; profile: ProfileConfig; access_token?: string | null }> =>
  (await api.put(`/api/profiles/${slug}`, body)).data

export const apiDeleteProfile = async (slug: string): Promise<{ status: string }> =>
  (await api.delete(`/api/profiles/${slug}`)).data

export const apiGenerateMitigations = async (sprintKey: string): Promise<MitigationResponse> => {
  const data = (await api.post('/api/generate-mitigations', { sprint_key: sprintKey })).data
  data.mitigations?.forEach((m: Mitigation) => {
    if (m.ai_used) {
      console.info(
        `[Agile Comrade] AI mitigation | source=LLM | provider=${m.llm?.provider ?? '?'} | sprint=${m.sprint_key}`,
      )
    } else {
      console.warn(
        `[Agile Comrade] AI mitigation | source=rule-based | provider=${m.llm?.provider ?? '?'} | error=${m.error ?? 'unknown'}`,
      )
    }
  })
  return data
}

export const apiNextSprintIssues = async (projectKey: string): Promise<{ issues: NextSprintIssue[] }> =>
  (await api.post('/api/next-sprint-issues', { project_key: projectKey })).data

export const apiNextSprintRisks = async (projectKey: string): Promise<NextSprintAnalysis> => {
  const data = (await api.post('/api/next-sprint-risks', { project_key: projectKey })).data
  if (data.ai_used) {
    console.info(
      `[Agile Comrade] AI next-sprint | source=LLM | provider=${data.llm?.provider ?? '?'} | project=${projectKey}`,
    )
  } else {
    console.warn(
      `[Agile Comrade] AI next-sprint | source=rule-based | provider=${data.llm?.provider ?? '?'} | error=${data.error ?? 'unknown'}`,
    )
  }
  return data
}

export const apiGenerateFollowup = async (
  issueKey: string,
  blocker?: Blocker,
): Promise<FollowupMessage> =>
  (
    await api.post('/api/generate-followup-message', {
      issue_key: issueKey,
      blocker: blocker ?? null,
    })
  ).data

export const apiSetRiskDecision = async (
  riskId: string,
  body: { status: RiskDecisionStatus; note?: string; owner?: string },
): Promise<{ status: string; risk_id: string; decision: RiskDecision }> =>
  (await api.post('/api/risk-decision', { risk_id: riskId, ...body })).data