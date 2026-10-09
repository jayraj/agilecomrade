// Single source of truth for risk severity: score bands, colors, RAG ordering,
// the 1..25 P×I matrix bands, and the CSS class / badge maps derived from them.
//
// Score bands must mirror backend/risk_components.py:bucket_severity
// (LOW<20, MEDIUM 20-59, HIGH 60-79, CRITICAL 80+). Matrix bands must mirror
// backend/risk_matrix.py:MATRIX_BANDS. Severity palette is anchored to the
// design-system semantic tokens: CRITICAL = error (#ef4444), MEDIUM = warning
// (#f59e0b), LOW = success (#10b981); HIGH uses a deepened warning (#d97706) to
// keep the four tiers distinguishable.

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

export const RISK_BANDS = [
  { sev: 'CRITICAL', band: '(80+)', min: 80, color: '#ef4444' },
  { sev: 'HIGH', band: '(60-79)', min: 60, color: '#d97706' },
  { sev: 'MEDIUM', band: '(20-59)', min: 20, color: '#f59e0b' },
  { sev: 'LOW', band: '(<20)', min: 0, color: '#10b981' },
] as const

const bandForScore = (score: number): (typeof RISK_BANDS)[number] =>
  RISK_BANDS.find((b) => score >= b.min) ?? RISK_BANDS[RISK_BANDS.length - 1]

export const getRiskColor = (score: number | undefined | null): string => {
  if (score === undefined || score === null) return '#a1a1aa'
  return bandForScore(score).color
}

export const severityFromScore = (score?: number | null): string | null => {
  if (score === undefined || score === null) return null
  return bandForScore(score).sev
}

// Canonical RAG ordering shared by every severity sort (higher = worse).
export const SEVERITY_RANK: Record<string, number> = {
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
}

// Resolve a blocker's display severity. The recalibrated score band wins when a
// score is present (it mirrors the backend's bucket_severity), then the stored
// severity field, then a MEDIUM default — always upper-cased for badge/class use.
export const severityOf = (b: {
  risk_score?: number | null
  severity?: string | null
}): string => (severityFromScore(b.risk_score) || b.severity || 'MEDIUM').toUpperCase()

// Per-driver score-math chips. Reads the structured `factors` block the backend
// attaches to each risk (band = score-band chips; drivers = the exact multipliers
// that went into the risk's raw score). Returns [] when the block is absent so
// callers fall back to the severity_reason text chip instead of breaking.
export const scoreDrivers = (risk: {
  factors?: {
    band?: string[]
    drivers?: { icon?: string; label?: string }[]
  } | null
}): { icon?: string; label: string }[] => {
  const factors = risk?.factors
  if (!factors) return []
  const band = (factors.band ?? []).filter(Boolean).map((label) => ({ label }))
  const drivers = (factors.drivers ?? []).map((c) => ({ icon: c.icon, label: c.label ?? '' }))
  return [...band, ...drivers].filter((c) => c.label)
}

// Severity band over the 1..25 P×I product (see backend/risk_matrix.py).
export const MATRIX_BANDS = [
  { max: 4, band: 'LOW' },
  { max: 9, band: 'MEDIUM' },
  { max: 14, band: 'HIGH' },
  { max: 25, band: 'CRITICAL' },
] as const

export const matrixBand = (value: number): string => {
  const hit = MATRIX_BANDS.find((b) => value <= b.max)
  return (hit ?? MATRIX_BANDS[MATRIX_BANDS.length - 1]).band
}

// Severity → CSS modifier class (RiskCardItem).
export const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: 'critical',
  HIGH: 'high',
  MEDIUM: 'medium',
  LOW: 'low',
}

// Severity → badge design tokens (SprintCard).
export const SEVERITY_BADGE: Record<string, { bg: string; text: string; dot: string; label: string }> = {
  CRITICAL: { bg: 'var(--badge-critical-bg)', text: 'var(--badge-critical-text)', dot: 'var(--badge-critical-dot)', label: 'critical' },
  HIGH: { bg: 'var(--badge-high-bg)', text: 'var(--badge-high-text)', dot: 'var(--badge-high-dot)', label: 'high' },
  MEDIUM: { bg: 'var(--badge-medium-bg)', text: 'var(--badge-medium-text)', dot: 'var(--badge-medium-dot)', label: 'medium' },
  LOW: { bg: 'var(--badge-low-bg)', text: 'var(--badge-low-text)', dot: 'var(--badge-low-dot)', label: 'low' },
}
