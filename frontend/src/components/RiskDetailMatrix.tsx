import { useState } from 'react'
import { ChevronRight, Grid3x3 } from 'lucide-react'

const PROBABILITY = [
  { label: 'Rare', score: 1, odds: '~4%' },
  { label: 'Unlikely', score: 2, odds: '~8%' },
  { label: 'Possible', score: 3, odds: '~12%' },
  { label: 'Likely', score: 4, odds: '~16%' },
  { label: 'Almost Certain', score: 5, odds: '~20%' },
]

const IMPACT = [
  { label: 'Insignificant', score: 1 },
  { label: 'Minor', score: 2 },
  { label: 'Moderate', score: 3 },
  { label: 'Major', score: 4 },
  { label: 'Critical', score: 5 },
]

const SCALE = [
  { max: 8, band: 'LOW' },
  { max: 12, band: 'MEDIUM' },
  { max: 16, band: 'HIGH' },
  { max: 25, band: 'CRITICAL' },
]

const bandFor = (value: number): string => {
  const hit = SCALE.find((s) => value <= s.max)
  return (hit ?? SCALE[SCALE.length - 1]).band
}

export default function RiskDetailMatrix() {
  const [open, setOpen] = useState(false)

  return (
    <div className="risk-matrix">
      <button
        type="button"
        className="risk-matrix-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight size={14} className={open ? 'risk-matrix-chevron open' : 'risk-matrix-chevron'} />
        <Grid3x3 size={14} />
        <span className="risk-matrix-toggle-label">Risk score matrix</span>
        <span className="risk-matrix-toggle-hint">Probability × Impact — reference only</span>
      </button>

      {open && (
        <div className="risk-matrix-body">
          <p className="risk-matrix-caption">
            Reference scale for reading risk severity. Scoring is not wired to this matrix yet.
          </p>
          <div className="risk-matrix-grid">
            <div className="risk-matrix-corner">
              <span className="risk-matrix-axis">Probability ↓</span>
              <span className="risk-matrix-axis">Impact →</span>
            </div>
            {IMPACT.map((impact) => (
              <div key={impact.score} className="risk-matrix-col-head">
                <span className="risk-matrix-col-score">{impact.score}</span>
                <span className="risk-matrix-col-label">{impact.label}</span>
              </div>
            ))}
            {PROBABILITY.map((prob) => (
              <div key={prob.score} className="risk-matrix-row">
                <div className="risk-matrix-row-head">
                  <span className="risk-matrix-row-label">{prob.label}</span>
                  <span className="risk-matrix-row-odds">
                    {prob.score} · {prob.odds}
                  </span>
                </div>
                {IMPACT.map((impact) => {
                  const value = prob.score * impact.score
                  const band = bandFor(value)
                  return (
                    <div
                      key={`${prob.score}-${impact.score}`}
                      className={`risk-matrix-cell ${band.toLowerCase()}`}
                      title={`${prob.label} × ${impact.label} = ${value} (${band})`}
                    >
                      <span className="risk-matrix-cell-value">{value}</span>
                      <span className="risk-matrix-cell-band">{band}</span>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
