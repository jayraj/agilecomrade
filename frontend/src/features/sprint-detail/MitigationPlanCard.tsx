import { ShieldCheck } from 'lucide-react'
import type { Mitigation } from '../../api/client'
import { describeAiFallback, formatRiskType, splitItems } from '../../utils/format'

interface MitigationPlanCardProps {
  mitigation: Mitigation
  showDebug: boolean
}

export default function MitigationPlanCard({ mitigation, showDebug }: MitigationPlanCardProps) {
  return (
    <div className="mitigation-card">
      {mitigation.ai_used === false && (
        <div className="ai-fallback-note">{describeAiFallback(mitigation.fallback_reason)}</div>
      )}
      <h4 className="mitigation-title">
        <ShieldCheck size={20} className="title-icon" />AI MITIGATION PLAN
      </h4>

      {(mitigation.risk_types ?? []).length > 0 && (
        <div className="risk-chips">
          {mitigation.risk_types?.map((type) => (
            <span key={type} className="risk-chip">
              {formatRiskType(type)}
              {type === 'BURNDOWN_BEHIND' &&
                mitigation.burndown_gap_percent !== undefined &&
                mitigation.burndown_gap_percent !== null && (
                  <> — {mitigation.burndown_gap_percent}%</>
                )}
            </span>
          ))}
        </div>
      )}

      {mitigation.action_items && mitigation.action_items.length > 0 && (
        <div className="plan-section">
          <div className="plan-section-title">ACTION ITEMS</div>
          <ol className="plan-list">
            {mitigation.action_items.map((action, idx) => (
              <li key={idx}>{action}</li>
            ))}
          </ol>
        </div>
      )}

      {mitigation.owner && (
        <div className="plan-section">
          <div className="plan-section-title">OWNER</div>
          <ul className="plan-list">
            {splitItems(mitigation.owner).map((item, idx) => (
              <li key={`o${idx}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {mitigation.timeline && (
        <div className="plan-section">
          <div className="plan-section-title">TIMELINE</div>
          <ul className="plan-list">
            {splitItems(mitigation.timeline).map((item, idx) => (
              <li key={`t${idx}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {mitigation.success_criteria && mitigation.success_criteria.length > 0 && (
        <div className="plan-section">
          <div className="plan-section-title">SUCCESS CRITERIA</div>
          <ul className="plan-list">
            {mitigation.success_criteria.map((c, idx) => (
              <li key={idx}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {showDebug && (
        <details className="prompt-details">
          <summary>🔍 View AI Prompt &amp; Raw Response</summary>
          {mitigation.llm && (
            <div className="ai-info-line">
              <span>🤖 LLM: {mitigation.llm.provider} · {mitigation.llm.model}</span>
              <span className={`ai-used ${mitigation.ai_used ? 'yes' : 'no'}`}>
                {mitigation.ai_used ? 'AI used' : 'Rule-based fallback'}
              </span>
            </div>
          )}
          <div className="prompt-block">
            <div className="prompt-label">Prompt sent to model:</div>
            <pre>{mitigation.prompt}</pre>
            <div className="prompt-label">Model response:</div>
            <pre>{mitigation.raw_response || '⚠️ AI unavailable — used fallback (see error).'}</pre>
          </div>
        </details>
      )}
    </div>
  )
}
