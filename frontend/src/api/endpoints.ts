import { api } from './client'
import type {
  Blocker,
  FollowupMessage,
  Mitigation,
  MitigationResponse,
  NextSprintAnalysis,
  NextSprintIssue,
  ProfileConfig,
  RiskDecision,
  RiskDecisionStatus,
  Snapshot,
  TestConfigResult,
} from './types'

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
