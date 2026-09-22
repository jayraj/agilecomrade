import { useState } from 'react'
import { AlertCircle } from 'lucide-react'
import {
  describeAiFallback,
  riskTitle,
  severityFromScore,
  formatRiskType,
  sanitizeInlineHtml,
  sprintOverdueDays,
} from '../utils/format'
import type { Blocker, RiskDecisionStatus } from '../api/client'

interface RiskCardItemProps {
  blocker: Blocker
  endDate?: string
  showDraft?: boolean
  drafting?: boolean
  onDraft?: () => void
  draft?: string
  generatedBy?: string
  fallbackReason?: string
  onCopy?: () => void
  onDecide?: (status: RiskDecisionStatus, note: string) => Promise<void> | void
  deciding?: boolean
  offline?: boolean
}

const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
}

const DECISION_OPTIONS: RiskDecisionStatus[] = [
  'accepted',
  'monitoring',
  'mitigating',
  'escalated',
  'dismissed',
]

const DECISION_META: Record<string, { label: string; className: string }> = {
  accepted: { label: 'Accepted', className: 'accepted' },
  monitoring: { label: 'Monitoring', className: 'monitoring' },
  mitigating: { label: 'Mitigating', className: 'mitigating' },
  escalated: { label: 'Escalated', className: 'escalated' },
  dismissed: { label: 'Dismissed', className: 'dismissed' },
  pending: { label: 'Decision needed', className: 'pending' },
}

const TRANSPARENCY_LABEL = 'risk-transparency-label'

export default function RiskCardItem({
  blocker,
  endDate,
  showDraft,
  drafting,
  onDraft,
  draft,
  generatedBy,
  fallbackReason,
  onCopy,
  onDecide,
  deciding,
  offline,
}: RiskCardItemProps) {
  const severity = (severityFromScore(blocker.risk_score) || blocker.severity || 'MEDIUM').toUpperCase()
  const sevClass = SEVERITY_CLASS[severity] || 'medium'
  const title = riskTitle(blocker)
  const categoryLabel = blocker.type ? formatRiskType(blocker.type) : ''
  const issueKey = blocker.issue_key

  const [pendingStatus, setPendingStatus] = useState<RiskDecisionStatus | null>(null)
  const [note, setNote] = useState('')
  const [noteVisible, setNoteVisible] = useState(false)

  const status = pendingStatus ?? blocker.decision?.status ?? 'pending'
  const decisionMeta = DECISION_META[status] ?? DECISION_META.pending
  const decision = blocker.decision?.note || note
  const decisionsEnabled = !!onDecide && !deciding && !offline

  const handleDecide = (next: RiskDecisionStatus): void => {
    setPendingStatus(next)
    setNoteVisible(true)
    void onDecide?.(next, note)
  }

  const saveNote = (): void => {
    if (status === 'pending' || !onDecide) return
    void onDecide(status, note)
  }

  return (
    <div className={`risk-card-item ${sevClass}`}>
      <div className="risk-card-item-header">
        <AlertCircle size={12} className="risk-card-item-icon" />
        <span className="risk-card-item-sev">
        {blocker.severity_reason ? (
          <span className="sev-with-tip">
            {severity.toLowerCase()}
            <span className="sev-tip">{blocker.severity_reason}</span>
          </span>
        ) : (
          severity.toLowerCase()
        )}
        {categoryLabel && (
          <>
            <span className="risk-card-item-sep"> · </span>
            <span className="risk-card-item-cat">{categoryLabel}</span>
          </>
        )}
      </span>
      </div>
      <p className="risk-card-item-title">{title}</p>

      {blocker.signal && (
        <div className="risk-signal">
          <div className={TRANSPARENCY_LABEL}>
            Signal
            {blocker.risk_score != null && (
              <span className="risk-score-text"> (Risk score: {Math.round(blocker.risk_score)})</span>
            )}
          </div>
          <div className="risk-signal-headline">{blocker.signal.label}</div>
          {blocker.signal.facts.length > 0 && (
            <div className="risk-fact-list">
              {blocker.signal.facts.map((fact, i) => {
                const overdue = fact.label === 'Days remaining' ? sprintOverdueDays(endDate) : null
                const display =
                  overdue !== null
                    ? { label: 'Overdue by', value: `${overdue} day${overdue === 1 ? '' : 's'}` }
                    : fact
                return (
                  <div className="risk-fact" key={i}>
                    <span className="risk-fact-label">{display.label}</span>
                    <span className="risk-fact-value">{display.value}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {blocker.suspected_cause && (
        <div className="risk-cause">
          <div className={TRANSPARENCY_LABEL}>Suspected cause</div>
          <p className="risk-cause-text">{blocker.suspected_cause}</p>
        </div>
      )}

      {(blocker.suggested_action || ((showDraft || draft) && issueKey)) && (
        <div className="risk-action">
          <div className={TRANSPARENCY_LABEL}>Suggested action</div>
          {blocker.suggested_action && <p className="risk-action-text">{blocker.suggested_action}</p>}
          {showDraft && issueKey && (
            <button className="draft-btn" disabled={drafting} onClick={onDraft}>
              {drafting ? 'Drafting...' : '💬 Draft Message'}
            </button>
          )}
          {issueKey && draft && (
            <div className="draft-output">
              <div className="draft-output-header">
                <span>✍️ AI Follow-up Message</span>
                <div className="draft-output-actions">
                  <button className="copy-btn" onClick={onCopy}>📋 Copy</button>
                </div>
              </div>
              <p className="draft-text" dangerouslySetInnerHTML={{ __html: sanitizeInlineHtml(draft) }} />
              <div className="fallback-note">Paste this into the Jira ticket as a comment.</div>
              {generatedBy === 'rule-based' && (
                <div className="fallback-note">{describeAiFallback(fallbackReason || '')}</div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="risk-decision">
        <div className="risk-decision-head">
          <div className={TRANSPARENCY_LABEL}>Human decision</div>
          <span className={`risk-decision-chip risk-decision-chip--${decisionMeta.className}`}>
            {decisionMeta.label}
            {deciding && <span className="risk-decision-deciding"> saving…</span>}
          </span>
        </div>
        {onDecide && (
          <>
            <div className="risk-decision-actions">
              {DECISION_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  className={`risk-decision-btn ${status === option ? 'risk-decision-btn--active' : ''}`}
                  onClick={() => handleDecide(option)}
                  disabled={!decisionsEnabled}
                >
                  {option}
                </button>
              ))}
            </div>
            {noteVisible && (
              <div className="risk-decision-note-row">
                <input
                  className="risk-decision-note-input"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') saveNote()
                  }}
                  placeholder="Add a note (why)?"
                />
                <button type="button" className="copy-btn" disabled={deciding || offline} onClick={saveNote}>
                  Save note
                </button>
              </div>
            )}
          </>
        )}
        {decision && <p className="risk-decision-note">{decision}</p>}
      </div>
    </div>
  )
}