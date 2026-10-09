import { useEffect, useState } from 'react'
import { useSnapshot } from '../../hooks/useSnapshot'
import { useSync } from '../../context/SyncContext'
import {
  apiGenerateFollowup,
  apiGenerateMitigations,
  apiNextSprintIssues,
  apiNextSprintRisks,
  apiSetRiskDecision,
  type Blocker,
  type Mitigation,
  type NextSprintAnalysis,
  type NextSprintIssue,
  type NextSprintProject,
  type RadarCard,
  type RiskDecision,
  type RiskDecisionStatus,
  type Snapshot,
} from '../../api'
import { sprintDayLabel, draftToPlainText } from '../../utils/format'
import { SEVERITY_RANK, severityFromScore } from '../../utils/severity'

export interface SprintStat {
  label: string
  value: string
  sub?: string
  tone: 'progress' | 'remaining'
}

interface WorkItem {
  key?: string
  summary?: string
  status?: string
  assignee?: string
  story_points?: number
}

export interface SprintDetail {
  // load status
  snapshot: Snapshot | null
  loading: boolean
  error: string | null
  noProfile: boolean
  offline: boolean
  // identity / summary
  isFuture: boolean
  projectKey?: string
  dayLabel: string | null
  start?: string
  end?: string
  riskScore: number
  stats: SprintStat[]
  // issues
  sprintBlockers: Blocker[]
  // AI plan
  sprintMitigation: Mitigation | null
  planVisible: boolean
  generating: boolean
  analyzing: boolean
  futureAnalysis: NextSprintAnalysis | null
  requestPlan: () => void
  // work items
  workItems: WorkItem[]
  totalSp: number
  riskSeverityByKey: Map<string, string>
  // drafting
  draftingKey: string | null
  drafts: Record<string, string>
  draftGeneratedBy: Record<string, string>
  draftFallbackReasons: Record<string, string>
  copiedKey: string | null
  draftMessage: (blocker: Blocker) => Promise<void>
  copyDraft: (issueKey: string) => Promise<void>
  // decisions
  localDecisions: Record<string, RiskDecision>
  decidingId: string | null
  decideRisk: (blocker: Blocker, status: RiskDecisionStatus, note: string) => Promise<void>
}

export function useSprintDetail(kind: 'active' | 'future', sprintKey: string): SprintDetail {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, error, noProfile, offline } = useSnapshot(syncIntervalSeconds, refreshKey)
  const isFuture = kind === 'future'

  // The freshly generated plan takes precedence; otherwise fall back to the
  // plan persisted on the snapshot so it survives a hard refresh.
  const [generatedMitigations, setGeneratedMitigations] = useState<Mitigation[] | null>(null)
  const mitigations: Mitigation[] = generatedMitigations ?? snapshot?.mitigations ?? []
  const [generating, setGenerating] = useState(false)
  const [draftingKey, setDraftingKey] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [draftGeneratedBy, setDraftGeneratedBy] = useState<Record<string, string>>({})
  const [draftFallbackReasons, setDraftFallbackReasons] = useState<Record<string, string>>({})
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [planRequestedFor, setPlanRequestedFor] = useState<string | null>(null)

  const [futureIssues, setFutureIssues] = useState<NextSprintIssue[]>([])
  const [futureRisks, setFutureRisks] = useState<Blocker[] | null>(null)
  const [futureAnalysis, setFutureAnalysis] = useState<NextSprintAnalysis | null>(null)
  const [analyzing, setAnalyzing] = useState(false)

  const [localDecisions, setLocalDecisions] = useState<Record<string, RiskDecision>>({})
  const [decidingId, setDecidingId] = useState<string | null>(null)

  const project: NextSprintProject | undefined = !isFuture
    ? undefined
    : snapshot?.next_sprint_overview?.projects.find((p) => p.project_key === sprintKey)

  const card: RadarCard | NextSprintProject | null = isFuture
    ? project ?? null
    : snapshot?.radar_data.find((r) => r.sprint_key === sprintKey) ?? null

  const sevRank = SEVERITY_RANK
  const sortBlockersBySeverity = (list: Blocker[]): Blocker[] =>
    [...list].sort((a, b) => {
      const sa = sevRank[(a.severity ?? severityFromScore(a.risk_score)) ?? ''] ?? 0
      const sb = sevRank[(b.severity ?? severityFromScore(b.risk_score)) ?? ''] ?? 0
      return sb - sa
    })

  const sprintBlockers: Blocker[] = sortBlockersBySeverity(
    isFuture
      ? futureRisks ?? []
      : sprintKey
        ? (snapshot?.blockers ?? []).filter((b) => b.sprint_key === sprintKey)
        : [],
  )

  const sprintDataEntry = sprintKey
    ? Object.values(snapshot?.sprint_data ?? {}).find((d) => d.sprint?.name === sprintKey)
    : undefined
  const workItems: WorkItem[] = isFuture ? futureIssues : (sprintDataEntry?.issues ?? [])

  const riskSeverityByKey = new Map<string, string>()
  if (!isFuture) {
    for (const b of sprintBlockers) {
      const keys = b.issue_keys?.length ? b.issue_keys : b.issue_key ? [b.issue_key] : []
      const sev = b.severity ?? severityFromScore(b.risk_score)
      for (const k of keys) {
        if (!sev) continue
        const cur = riskSeverityByKey.get(k)
        if (!cur || (sevRank[sev] ?? 0) > (sevRank[cur] ?? 0)) riskSeverityByKey.set(k, sev)
      }
    }
  }

  const projectKey = isFuture ? project?.project_key : (card as { project_key?: string } | null)?.project_key
  const start = card?.start_date
  const end = card?.end_date
  const totalSp = card?.total_sp ?? 0
  const completedSp = isFuture ? 0 : ((card as { completed_sp?: number } | null)?.completed_sp ?? 0)
  const progressPct = totalSp > 0 ? Math.round((completedSp / totalSp) * 100) : 0
  const remainingSp = Math.max(totalSp - completedSp, 0)
  const riskScore = card?.risk_score ?? 0
  const dayLabel = sprintDayLabel(start, end)

  useEffect(() => {
    if (!isFuture || !project) return
    let cancelled = false
    if (project.issue_count > 0) {
      apiNextSprintIssues(project.project_key)
        .then((details) => {
          if (!cancelled) setFutureIssues(details.issues)
        })
        .catch((e) => console.error('Error loading issues:', e))
    }
    return () => {
      cancelled = true
    }
  }, [isFuture, project])

  const analyzeRisks = async () => {
    if (!project) return
    setAnalyzing(true)
    try {
      const response = await apiNextSprintRisks(project.project_key)
      setFutureAnalysis(response)
      setFutureRisks(
        (response.risks || []).sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)),
      )
    } catch (e) {
      console.error('Error analyzing next sprint risks:', e)
      setFutureAnalysis(null)
      setFutureRisks([])
    } finally {
      setAnalyzing(false)
    }
  }

  const generateMitigations = async () => {
    if (!sprintKey) return
    setGenerating(true)
    try {
      const response = await apiGenerateMitigations(sprintKey)
      setGeneratedMitigations(response.mitigations)
    } catch (e) {
      console.error('Error generating mitigations:', e)
    } finally {
      setGenerating(false)
    }
  }

  const requestPlan = () => {
    setPlanRequestedFor(sprintKey)
    if (isFuture) void analyzeRisks()
    else void generateMitigations()
  }

  const draftMessage = async (blocker: Blocker) => {
    const issueKey = blocker.issue_key
    if (!issueKey) return
    setDraftingKey(issueKey)
    try {
      const response = await apiGenerateFollowup(issueKey, blocker)
      setDrafts((prev) => ({ ...prev, [issueKey]: response.message || '' }))
      setDraftGeneratedBy((prev) => ({ ...prev, [issueKey]: response.generated_by || 'ai' }))
      setDraftFallbackReasons((prev) => ({ ...prev, [issueKey]: response.fallback_reason || '' }))
    } catch (e) {
      console.error('Error drafting message:', e)
      setDrafts((prev) => ({ ...prev, [issueKey]: 'Failed to generate message. Please try again.' }))
    } finally {
      setDraftingKey(null)
    }
  }

  const copyDraft = async (issueKey: string) => {
    const text = drafts[issueKey]
    if (!text) return
    try {
      await navigator.clipboard.writeText(draftToPlainText(text))
      setCopiedKey(issueKey)
      setTimeout(() => setCopiedKey((k) => (k === issueKey ? null : k)), 2000)
    } catch (e) {
      console.error('Copy failed:', e)
    }
  }

  const decideRisk = async (blocker: Blocker, status: RiskDecisionStatus, note: string) => {
    const riskId = blocker.risk_id
    if (!riskId) return
    setDecidingId(riskId)
    try {
      const response = await apiSetRiskDecision(riskId, { status, note })
      setLocalDecisions((prev) => ({ ...prev, [riskId]: response.decision }))
    } catch (e) {
      console.error('Error recording risk decision:', e)
    } finally {
      setDecidingId(null)
    }
  }

  const sprintMitigation = mitigations.find((m) => m.sprint_key === sprintKey) || null
  const planVisible = !!sprintKey && (planRequestedFor === sprintKey || sprintMitigation !== null)

  const stats: SprintStat[] = isFuture
    ? [
        { label: 'Work Items', value: `${project?.issue_count ?? 0}`, tone: 'remaining' },
        { label: 'Story Points', value: `${totalSp}`, tone: 'progress' },
      ]
    : [
        { label: 'Progress', value: `${progressPct}%`, tone: 'progress' },
        { label: 'Remaining', value: `${remainingSp}pt`, sub: `of ${totalSp} planned`, tone: 'remaining' },
      ]

  return {
    snapshot,
    loading,
    error,
    noProfile,
    offline,
    isFuture,
    projectKey,
    dayLabel,
    start,
    end,
    riskScore,
    stats,
    sprintBlockers,
    sprintMitigation,
    planVisible,
    generating,
    analyzing,
    futureAnalysis,
    requestPlan,
    workItems,
    totalSp,
    riskSeverityByKey,
    draftingKey,
    drafts,
    draftGeneratedBy,
    draftFallbackReasons,
    copiedKey,
    draftMessage,
    copyDraft,
    localDecisions,
    decidingId,
    decideRisk,
  }
}
