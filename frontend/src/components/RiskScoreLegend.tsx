// Always-on score-band legend for score-math transparency. Renders the same
// band+driver chips the backend explainer attaches (`riskScoreLegend`), so the
// legend always matches the score math regardless of any single risk's payload.
// Fully self-contained: inline styles only, no new CSS tokens.
import { Layers } from 'lucide-react'
import { riskScoreLegend } from '../utils/format'

const fmt = (c: { icon?: string; label: string }) => c.label

export default function RiskScoreLegend() {
  const chips = riskScoreLegend()
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '10px 16px',
        alignItems: 'center',
        margin: '0 0 14px',
        padding: '10px 13px',
        border: '1px solid #e4e4e7',
        borderRadius: '8px',
        background: '#ffffff',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '6px',
          fontSize: '12px',
          fontWeight: 600,
          color: '#3f3f46',
        }}
      >
        <Layers size={13} />
        Score bands &amp; drivers
      </span>
      <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '6px' }}>
        {chips.map((c, i) => (
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
            {fmt(c)}
          </span>
        ))}
      </span>
    </div>
  )
}
