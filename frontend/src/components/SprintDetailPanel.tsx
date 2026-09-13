import { useEffect, useState } from 'react'
import {
  Calendar,
  Clock,
  ChevronRight,
  Layers,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react'
import { useSnapshot } from '../hooks/useSnapshot'
import { useSync } from '../context/SyncContext'
import {
  apiGenerateFollowup,
  apiGenerateMitigations,
  apiNextSprintIssues,
  apiNextSprintRisks,
  SHOW_AI_DEBUG,
  type Blocker,
  type Mitigation,
  type NextSprintIssue,
  type NextSprintProject,
} from '../api/client'
import {
  describeAiFallback,
  formatDate,
  formatRiskType,
  severityFromScore,
  splitItems,
  sprintDayLabel,
} from '../utils/format'
import SprintGauge from './SprintGauge'
import RiskCardItem from './RiskCardItem'
import WorkItemTable from './WorkItemTable'

export interface SprintDetailPanelProps {
  kind: 'active' | 'future'
  sprintKey: string
  onClose?: () => void
}

const draftToPlainText = (html: string): string =>
  html
    .replace(/<a\s+href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, (_m, url: string, label: string) => `${label} (${url})`)
    .replace(/<[^>]*>/g, '')

export default function SprintDetailPanel({ kind, sprintKey, onClose }: SprintDetailPanelProps) {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, error, noProfile, offline } = useSnapshot(syncIntervalSeconds, refreshKey)
  const isFuture = kind === 'future'

  const [mitigations, setMitigations] = useState<Mitigation[]>(snapshot?.mitigations ?? [])
  const [generating, setGenerating] = useState(false)
  const [draftingKey, setDraftingKey] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [draftGeneratedBy, setDraftGeneratedBy] = useState<Record<string, string>>({})
  const [draftFallbackReasons, setDraftFallbackReasons] = useState<Record<string, string>>({})
  const [planRequestedFor, setPlanRequestedFor] = useState<string | null>(null)

  const [futureIssues, setFutureIssues] = useState<NextSprintIssue[]>([])
  const [futureRisks, setFutureRisks] = useState<Blocker[] | null>(null)
  const [analyzing, setAnalyzing] = useState(false)

  const project: NextSprintProject | undefined = !isFuture
    ? undefined
    : snapshot?.next_sprint_overview?.projects.find((p) => p.project_key === sprintKey)

  const card = isFuture
    ? project
    : snapshot?.radar_data.find((r) => r.sprint_key === sprintKey) ?? null

  const sprintBlockers: Blocker[] = isFuture
    ? futureRisks ?? []
    : sprintKey
      ? (snapshot?.blockers ?? []).filter((b) => b.sprint_key === sprintKey)
      : []

  const sprintMitigation = mitigations.find((m) => m.sprint_key === sprintKey) || null
  const planVisible = !!sprintKey && planRequestedFor === sprintKey

  const sprintDataEntry = sprintKey
    ? Object.values(snapshot?.sprint_data ?? {}).find((d) => d.sprint?.name === sprintKey)
    : undefined
  const workItems = isFuture ? futureIssues : (sprintDataEntry?.issues ?? [])

  const sevRank: Record<string, number> = { LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4 }
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
      setFutureRisks((response.risks || []).sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0)))
    } catch (e) {
      console.error('Error analyzing next sprint risks:', e)
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
      setMitigations(response.mitigations)
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
      <header className="detail-shell-head">
        <div className="detail-shell-head-left">
          <div className="detail-shell-eyebrow-row">
            <span className="detail-shell-eyebrow">
              {projectKey ? `${projectKey} · ` : ''}Sprint Details
            </span>
            {dayLabel && <span className="detail-shell-day">{dayLabel}</span>}
          </div>
          <h1 className="detail-shell-title">{sprintKey}</h1>
          <div className="detail-shell-dates">
            {start && (
              <span className="detail-shell-date">
                <Calendar size={11} /> {formatDate(start)}
              </span>
            )}
            {start && end && <ChevronRight size={10} className="detail-shell-date-sep" />}
            {end && (
              <span className="detail-shell-date">
                <Clock size={11} /> {formatDate(end)}
              </span>
            )}
          </div>
        </div>
        <button className="detail-shell-close" onClick={onClose} aria-label="Close details">
          <X size={15} />
        </button>
      </header>

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
                return (
                  <RiskCardItem
                    key={key}
                    blocker={blocker}
                    showDraft={!isFuture && !!blocker.issue_key && !offline}
                    showCategory={isFuture}
                    drafting={draftingKey === blocker.issue_key}
                    onDraft={() => draftMessage(blocker)}
                    draft={blocker.issue_key ? drafts[blocker.issue_key] : undefined}
                    generatedBy={blocker.issue_key ? draftGeneratedBy[blocker.issue_key] : undefined}
                    fallbackReason={blocker.issue_key ? draftFallbackReasons[blocker.issue_key] : undefined}
                    onCopy={() => blocker.issue_key && copyDraft(blocker.issue_key)}
                  />
                )
              })}
            </div>
          </section>
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
          <div className="mitigation-card">
            {sprintMitigation.ai_used === false && (
              <div className="ai-fallback-note">{describeAiFallback(sprintMitigation.fallback_reason)}</div>
            )}
            <h4 className="mitigation-title">
              <ShieldCheck size={20} className="title-icon" />AI MITIGATION PLAN
            </h4>

            {(sprintMitigation.risk_types ?? []).length > 0 && (
              <div className="risk-chips">
                {sprintMitigation.risk_types?.map((type) => (
                  <span key={type} className="risk-chip">
                    {formatRiskType(type)}
                    {type === 'BURNDOWN_BEHIND' &&
                      sprintMitigation.burndown_gap_percent !== undefined &&
                      sprintMitigation.burndown_gap_percent !== null && (
                        <> — {sprintMitigation.burndown_gap_percent}%</>
                      )}
                  </span>
                ))}
              </div>
            )}

            {sprintMitigation.action_items && sprintMitigation.action_items.length > 0 && (
              <div className="plan-section">
                <div className="plan-section-title">ACTION ITEMS</div>
                <ol className="plan-list">
                  {sprintMitigation.action_items.map((action, idx) => (
                    <li key={idx}>{action}</li>
                  ))}
                </ol>
              </div>
            )}

            {sprintMitigation.owner && (
              <div className="plan-section">
                <div className="plan-section-title">OWNER</div>
                <ul className="plan-list">
                  {splitItems(sprintMitigation.owner).map((item, idx) => (
                    <li key={`o${idx}`}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {sprintMitigation.timeline && (
              <div className="plan-section">
                <div className="plan-section-title">TIMELINE</div>
                <ul className="plan-list">
                  {splitItems(sprintMitigation.timeline).map((item, idx) => (
                    <li key={`t${idx}`}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {sprintMitigation.success_criteria && sprintMitigation.success_criteria.length > 0 && (
              <div className="plan-section">
                <div className="plan-section-title">SUCCESS CRITERIA</div>
                <ul className="plan-list">
                  {sprintMitigation.success_criteria.map((c, idx) => (
                    <li key={idx}>{c}</li>
                  ))}
                </ul>
              </div>
            )}

            {SHOW_AI_DEBUG && (
              <details className="prompt-details">
                <summary>🔍 View AI Prompt &amp; Raw Response</summary>
                {sprintMitigation.llm && (
                  <div className="ai-info-line">
                    <span>🤖 LLM: {sprintMitigation.llm.provider} · {sprintMitigation.llm.model}</span>
                    <span className={`ai-used ${sprintMitigation.ai_used ? 'yes' : 'no'}`}>
                      {sprintMitigation.ai_used ? 'AI used' : 'Rule-based fallback'}
                    </span>
                  </div>
                )}
                <div className="prompt-block">
                  <div className="prompt-label">Prompt sent to model:</div>
                  <pre>{sprintMitigation.prompt}</pre>
                  <div className="prompt-label">Model response:</div>
                  <pre>{sprintMitigation.raw_response || '⚠️ AI unavailable — used fallback (see error).'}</pre>
                </div>
              </details>
            )}
          </div>
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
