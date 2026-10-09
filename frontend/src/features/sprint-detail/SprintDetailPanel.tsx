import { Layers, Sparkles } from 'lucide-react'
import { SHOW_AI_DEBUG } from '../../api'
import SprintGauge from '../dashboard/SprintGauge'
import SprintDetailHeader from './SprintDetailHeader'
import IssuesSection from './IssuesSection'
import MitigationPlanCard from './MitigationPlanCard'
import RiskDetailMatrix from './RiskDetailMatrix'
import WorkItemTable from './WorkItemTable'
import { useSprintDetail } from './useSprintDetail'

export interface SprintDetailPanelProps {
  kind: 'active' | 'future'
  sprintKey: string
  onClose?: () => void
}

export default function SprintDetailPanel({ kind, sprintKey, onClose }: SprintDetailPanelProps) {
  const d = useSprintDetail(kind, sprintKey)
  const isFuture = d.isFuture

  if (d.noProfile) {
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

  if (d.error) {
    return (
      <div className="detail-shell">
        <div className="detail-shell-body">
          <div className="loading">⚠️ {d.error} — check Settings → profile configuration.</div>
        </div>
      </div>
    )
  }

  if (d.loading) {
    return (
      <div className="detail-shell">
        <div className="detail-shell-body">
          <div className="loading">Loading sprint details...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="detail-shell">
      <SprintDetailHeader
        sprintKey={sprintKey}
        projectKey={d.projectKey}
        dayLabel={d.dayLabel}
        start={d.start}
        end={d.end}
        onClose={onClose}
      />

      <div className="detail-shell-body">
        <div className="detail-stats">
          <div className="detail-stat detail-stat-risk">
            <SprintGauge score={d.riskScore} size={64} />
          </div>
          {d.stats.map((s) => (
            <div key={s.label} className={`detail-stat detail-stat-${s.tone}`}>
              <span className="detail-stat-label">{s.label}</span>
              <span className="detail-stat-value">{s.value}</span>
              {s.sub && <span className="detail-stat-sub">{s.sub}</span>}
            </div>
          ))}
        </div>

        <RiskDetailMatrix />

        <IssuesSection
          blockers={d.sprintBlockers}
          isFuture={isFuture}
          end={d.end}
          offline={d.offline}
          localDecisions={d.localDecisions}
          decidingId={d.decidingId}
          draftingKey={d.draftingKey}
          drafts={d.drafts}
          draftGeneratedBy={d.draftGeneratedBy}
          draftFallbackReasons={d.draftFallbackReasons}
          copiedKey={d.copiedKey}
          onDraft={d.draftMessage}
          onCopy={d.copyDraft}
          onDecide={d.decideRisk}
        />

        {isFuture && SHOW_AI_DEBUG && d.futureAnalysis && (
          <details className="prompt-details" open>
            <summary>🔍 View AI Prompt &amp; Raw Response</summary>
            <div className="ai-info-line">
              <span>
                🤖 LLM: {d.futureAnalysis.llm?.provider ?? '?'} · {d.futureAnalysis.llm?.model ?? '?'}
              </span>
              <span className={`ai-used ${d.futureAnalysis.ai_used ? 'yes' : 'no'}`}>
                {d.futureAnalysis.ai_used ? 'AI used' : 'Rule-based fallback'}
              </span>
            </div>
            <div className="prompt-block">
              <div className="prompt-label">Prompt sent to model:</div>
              <pre>{d.futureAnalysis.prompt || '—'}</pre>
              <div className="prompt-label">Model response:</div>
              <pre>{d.futureAnalysis.raw_response || '⚠️ AI unavailable — used rule-based fallback (reason logged to the browser console).'}</pre>
            </div>
          </details>
        )}

        <button
          type="button"
          className="detail-ai-btn"
          onClick={d.requestPlan}
          disabled={d.generating || d.analyzing || d.offline}
          title={d.offline ? 'You are offline — AI features need a connection' : undefined}
        >
          <Sparkles size={15} />
          {isFuture ? (d.analyzing ? 'Analyzing...' : 'Scan with AI') : d.generating ? 'Generating...' : 'Mitigation Plan with AI'}
        </button>

        {d.planVisible && d.sprintMitigation && (
          <MitigationPlanCard mitigation={d.sprintMitigation} showDebug={SHOW_AI_DEBUG} />
        )}

        {d.workItems.length > 0 && (
          <section className="detail-section">
            <div className="detail-section-head">
              <Layers size={13} style={{ color: 'var(--color-primary-600)' }} />
              <h2>Work Items</h2>
              <span className="detail-count">{d.workItems.length}</span>
              <span className="detail-pts">{d.totalSp} pts</span>
            </div>
            <WorkItemTable items={d.workItems} riskSeverityByKey={d.riskSeverityByKey} />
          </section>
        )}
      </div>
    </div>
  )
}
