import { useEffect, useState } from 'react'
import { Layers, ShieldAlert, Sparkles } from 'lucide-react'
import { useSnapshot } from '../../hooks/useSnapshot'
import { useSync } from '../../context/SyncContext'
import {
  apiGenerateFollowup,
  apiGenerateMitigations,
  apiNextSprintIssues,
  apiNextSprintRisks,
  apiSetRiskDecision,
  SHOW_AI_DEBUG,
  type Blocker,
  type Mitigation,
  type NextSprintAnalysis,
  type NextSprintIssue,
  type NextSprintProject,
  type RiskDecision,
  type RiskDecisionStatus,
} from '../../api/client'
import { SEVERITY_RANK, severityFromScore, sprintDayLabel, draftToPlainText } from '../../utils/format'
import SprintGauge from '../dashboard/SprintGauge'
import SprintDetailHeader from './SprintDetailHeader'
import MitigationPlanCard from './MitigationPlanCard'
import RiskCardItem from './RiskCardItem'
import RiskDetailMatrix from './RiskDetailMatrix'
import WorkItemTable from './WorkItemTable'

export interface SprintDetailPanelProps {
  kind: 'active' | 'future'
  sprintKey: string
  onClose?: () => void
}

export default function SprintDetailPanel({ kind, sprintKey, onClose }: SprintDetailPanelProps) {
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

  const card = isFuture
    ? project
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

  const sprintMitigation = mitigations.find((m) => m.sprint_key === sprintKey) || null
  // Reveal a plan once the user requests one, or whenever a plan for this
  // sprint is already persisted — so a saved plan survives a hard refresh
  // instead of requiring a fresh (billable) regeneration.
  const planVisible = !!sprintKey && (planRequestedFor === sprintKey || sprintMitigation !== null)

  const sprintDataEntry = sprintKey
    ? Object.values(snapshot?.sprint_data ?? {}).find((d) => d.sprint?.name === sprintKey)
    : undefined
  const workItems = isFuture ? futureIssues : (sprintDataEntry?.issues ?? [])

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
      alert('Message copied to clipboard!')
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

  if (noProfile) {
    return (
      <div className="detail-shell">
        <div className="detail-shell-body">
          <div className="no-data">
            <p>No profile configured yet.</p>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="detail-shell">
        <div className="detail-shell-body">
          <div className="loading">⚠️ {error} — check Settings → profile configuration.</div>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="detail-shell">
        <div className="detail-shell-body">
          <div className="loading">Loading sprint details...</div>
        </div>
      </div>
    )
  }

  const stats: { label: string; value: string; sub?: string; tone: 'progress' | 'remaining' }[] = isFuture
    ? [
        { label: 'Work Items', value: `${project?.issue_count ?? 0}`, tone: 'remaining' },
        { label: 'Story Points', value: `${totalSp}`, tone: 'progress' },
      ]
    : [
        { label: 'Progress', value: `${progressPct}%`, tone: 'progress' },
        { label: 'Remaining', value: `${remainingSp}pt`, sub: `of ${totalSp} planned`, tone: 'remaining' },
      ]

  return (
    <div className="detail-shell">
      <SprintDetailHeader
        sprintKey={sprintKey}
        projectKey={projectKey}
        dayLabel={dayLabel}
        start={start}
        end={end}
        onClose={onClose}
      />

      <div className="detail-shell-body">
        <div className="detail-stats">
          <div className="detail-stat detail-stat-risk">
            <SprintGauge score={riskScore} size={64} />
          </div>
          {stats.map((s) => (
            <div key={s.label} className={`detail-stat detail-stat-${s.tone}`}>
              <span className="detail-stat-label">{s.label}</span>
              <span className="detail-stat-value">{s.value}</span>
              {s.sub && <span className="detail-stat-sub">{s.sub}</span>}
            </div>
          ))}
        </div>

        <RiskDetailMatrix />

        {sprintBlockers.length > 0 && (
          <section className="detail-section">
            <div className="detail-section-head">
              <ShieldAlert size={13} style={{ color: 'var(--badge-critical-text)' }} />
              <h2>Issues</h2>
              <span className="detail-count">{sprintBlockers.length}</span>
            </div>
            <div className="detail-issues">
              {sprintBlockers.map((blocker, i) => {
                const key = blocker.issue_key || `${blocker.type}-${blocker.sprint_key}-${i}`
                const decision = blocker.risk_id
                  ? localDecisions[blocker.risk_id] ?? blocker.decision
                  : blocker.decision
                const canDecide = !isFuture && !!blocker.risk_id && !offline
                return (
                  <RiskCardItem
                    key={key}
                    blocker={{ ...blocker, decision }}
                    endDate={end}
                    hideSignal={isFuture}
                    showDraft={!isFuture && !!blocker.issue_key && !offline}
                    drafting={draftingKey === blocker.issue_key}
                    onDraft={() => draftMessage(blocker)}
                    draft={blocker.issue_key ? drafts[blocker.issue_key] : undefined}
                    generatedBy={blocker.issue_key ? draftGeneratedBy[blocker.issue_key] : undefined}
                    fallbackReason={blocker.issue_key ? draftFallbackReasons[blocker.issue_key] : undefined}
                    onCopy={() => blocker.issue_key && copyDraft(blocker.issue_key)}
                    onDecide={
                      canDecide
                        ? async (status, note) => {
                            await decideRisk(blocker, status, note)
                          }
                        : undefined
                    }
                    deciding={decidingId === blocker.risk_id}
                    offline={offline}
                  />
                )
              })}
            </div>
          </section>
        )}

        {isFuture && SHOW_AI_DEBUG && futureAnalysis && (
          <details className="prompt-details" open>
            <summary>🔍 View AI Prompt &amp; Raw Response</summary>
            <div className="ai-info-line">
              <span>
                🤖 LLM: {futureAnalysis.llm?.provider ?? '?'} · {futureAnalysis.llm?.model ?? '?'}
              </span>
              <span className={`ai-used ${futureAnalysis.ai_used ? 'yes' : 'no'}`}>
                {futureAnalysis.ai_used ? 'AI used' : 'Rule-based fallback'}
              </span>
            </div>
            <div className="prompt-block">
              <div className="prompt-label">Prompt sent to model:</div>
              <pre>{futureAnalysis.prompt || '—'}</pre>
              <div className="prompt-label">Model response:</div>
              <pre>{futureAnalysis.raw_response || '⚠️ AI unavailable — used rule-based fallback (reason logged to the browser console).'}</pre>
            </div>
          </details>
        )}

        <button
          className="detail-ai-btn"
          onClick={() => {
            setPlanRequestedFor(sprintKey)
            if (isFuture) analyzeRisks()
            else generateMitigations()
          }}
          disabled={generating || analyzing || offline}
          title={offline ? 'You are offline — AI features need a connection' : undefined}
        >
          <Sparkles size={15} />
          {isFuture ? (analyzing ? 'Analyzing...' : 'Scan with AI') : generating ? 'Generating...' : 'Mitigation Plan with AI'}
        </button>

        {planVisible && sprintMitigation && (
          <MitigationPlanCard mitigation={sprintMitigation} showDebug={SHOW_AI_DEBUG} />
        )}

        {workItems.length > 0 && (
          <section className="detail-section">
            <div className="detail-section-head">
              <Layers size={13} style={{ color: 'var(--color-primary-600)' }} />
              <h2>Work Items</h2>
              <span className="detail-count">{workItems.length}</span>
              <span className="detail-pts">{totalSp} pts</span>
            </div>
            <WorkItemTable items={workItems} riskSeverityByKey={riskSeverityByKey} />
          </section>
        )}

      </div>
    </div>
  )
}
