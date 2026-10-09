import { ShieldAlert } from 'lucide-react'
import type { Blocker, RiskDecision, RiskDecisionStatus } from '../../api'
import RiskCardItem from './RiskCardItem'

export interface IssuesSectionProps {
  blockers: Blocker[]
  isFuture: boolean
  end?: string
  offline: boolean
  localDecisions: Record<string, RiskDecision>
  decidingId: string | null
  draftingKey: string | null
  drafts: Record<string, string>
  draftGeneratedBy: Record<string, string>
  draftFallbackReasons: Record<string, string>
  copiedKey: string | null
  onDraft: (blocker: Blocker) => void
  onCopy: (issueKey: string) => void
  onDecide: (blocker: Blocker, status: RiskDecisionStatus, note: string) => Promise<void>
}

export default function IssuesSection({
  blockers,
  isFuture,
  end,
  offline,
  localDecisions,
  decidingId,
  draftingKey,
  drafts,
  draftGeneratedBy,
  draftFallbackReasons,
  copiedKey,
  onDraft,
  onCopy,
  onDecide,
}: IssuesSectionProps) {
  if (blockers.length === 0) return null

  return (
    <section className="detail-section">
      <div className="detail-section-head">
        <ShieldAlert size={13} style={{ color: 'var(--badge-critical-text)' }} />
        <h2>Issues</h2>
        <span className="detail-count">{blockers.length}</span>
      </div>
      <div className="detail-issues">
        {blockers.map((blocker, i) => {
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
              showRegister={!isFuture && !offline}
              drafting={draftingKey === blocker.issue_key}
              onDraft={() => onDraft(blocker)}
              draft={blocker.issue_key ? drafts[blocker.issue_key] : undefined}
              generatedBy={blocker.issue_key ? draftGeneratedBy[blocker.issue_key] : undefined}
              fallbackReason={blocker.issue_key ? draftFallbackReasons[blocker.issue_key] : undefined}
              onCopy={() => blocker.issue_key && onCopy(blocker.issue_key)}
              copied={!!blocker.issue_key && copiedKey === blocker.issue_key}
              onDecide={
                canDecide
                  ? async (status, note) => {
                      await onDecide(blocker, status, note)
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
  )
}
