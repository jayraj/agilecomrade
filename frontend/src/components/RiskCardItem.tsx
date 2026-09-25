import { useState } from 'react'
import { AlertCircle } from 'lucide-react'
import {
  describeAiFallback,
  riskTitle,
  severityFromScore,
  formatRiskType,
  sanitizeInlineHtml,
  sprintOverdueDays,
  scoreDrivers,
} from '../utils/format'
import type { Blocker, RiskDecisionStatus } from '../api/client'

interface RiskCardItemProps {
  blocker: Blocker
  endDate?: string
  hideSignal?: boolean
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

// ROAM (Scaled Agile) risk dispositions: Resolved / Owned / Accepted / Mitigated.
const DECISION_OPTIONS: RiskDecisionStatus[] = [
  'resolved',
  'owned',
  'accepted',
  'mitigated',
]

const DECISION_META: Record<string, { label: string; short: string; className: string }> = {
  resolved: { label: 'Resolved', short: 'resolved', className: 'resolved' },
  owned: { label: 'Owned', short: 'owned', className: 'owned' },
  accepted: { label: 'Accepted', short: 'accepted', className: 'accepted' },
  mitigated: { label: 'Mitigated', short: 'mitigated', className: 'mitigated' },
  pending: { label: 'Decision needed', short: 'needed', className: 'pending' },
}

const TRANSPARENCY_LABEL = 'risk-transparency-label'

// Normalize legacy recommendation wording so the shown Suggested action is always
// the current copy even if a stale backend serve the old sentence.
const ACTION_REWRITE: Record<string, string> = {
  'Clarify acceptance criteria before planning.': 'Align with Business/PO and clarify Acceptance Criteria before planning.',
}

const patchAction = (text?: string): string | undefined => {
  if (!text) return undefined
  let out = text
  for (const [from, to] of Object.entries(ACTION_REWRITE)) {
    out = out.includes(from) ? out.replace(from, to) : out
  }
  return out
}

export default function RiskCardItem({
  blocker,
  endDate,
  hideSignal,
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

  const rec = (blocker.recommendation || '').trim()
  const recParts = rec.match(/^(.*?[.!?])\s+(.*)$/)
  const suspectedCause =
    blocker.suspected_cause || (blocker.suggested_action ? undefined : recParts?.[1] || undefined)
  const suggestedAction = blocker.suggested_action
    ? patchAction(blocker.suggested_action)
    : rec
      ? patchAction(recParts?.[2] || rec)
      : undefined

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
        {severity.toLowerCase()}
        {categoryLabel && (
          <>
            <span className="risk-card-item-sep"> · </span>
            <span className="risk-card-item-cat">{categoryLabel}</span>
          </>
        )}
      </span>
      </div>
      <p className="risk-card-item-title">{title}</p>

      {!hideSignal && blocker.signal && (
        <div className="risk-signal">
          <div className={TRANSPARENCY_LABEL}>
            Risk Signals
            {blocker.risk_score != null && (
              <span className="risk-score-text"> (Score: {Math.round(blocker.risk_score)})</span>
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

          {scoreDrivers(blocker).length > 0 && (
            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: '6px',
                marginTop: '10px',
              }}
            >
              {scoreDrivers(blocker).map((c, i) => (
                <span
                  key={i}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '5px',
                    fontSize: '11px',
                    fontWeight: 600,
                    color: '#3f3f46',
                    background: '#f4f4f5',
                    border: '1px solid #e4e4e7',
                    borderRadius: '999px',
                    padding: '3px 9px',
                  }}
                >
                  {c.icon && <span aria-hidden>{c.icon}</span>}
                  {c.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {suspectedCause && (
        <div className="risk-cause">
          <div className={TRANSPARENCY_LABEL}>Suspected cause</div>
          <p className="risk-cause-text">{suspectedCause}</p>
        </div>
      )}

      {(suggestedAction || ((showDraft || draft) && issueKey)) && (
        <div className="risk-action">
          <div className={TRANSPARENCY_LABEL}>Suggested actions</div>
          {suggestedAction && <p className="risk-action-text">{suggestedAction}</p>}
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
          <div
            className={
              status !== 'pending'
                ? `${TRANSPARENCY_LABEL} risk-decision-chip risk-decision-chip--${decisionMeta.className}`
                : TRANSPARENCY_LABEL
            }
          >
            Human decision ({decisionMeta.short})
            {deciding && <span className="risk-decision-deciding"> saving…</span>}
          </div>
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