// Severity palette anchored to the design-system semantic tokens:
// CRITICAL = error (#ef4444), MEDIUM = warning (#f59e0b), LOW = success (#10b981).
// HIGH uses a deepened warning (#d97706) to keep the four tiers distinguishable.

// The Jira timezone (from the user's Jira profile, carried on the snapshot) is
// the single source of truth for calendar-day math, so every date we show
// matches what the user sees in Jira — regardless of the viewer's machine tz.
// null => fall back to the browser's local timezone (pre-sync / unknown).
let displayTimezone: string | null = null

export const setJiraTimezone = (tz: string | null | undefined): void => {
  displayTimezone = tz || null
}

export const getRiskColor = (score: number | undefined | null): string => {
  if (score === undefined || score === null) return '#a1a1aa'
  if (score >= 80) return '#ef4444'
  if (score >= 60) return '#d97706'
  if (score >= 20) return '#f59e0b'
  return '#10b981'
}

// Must mirror backend/risk_components.py:bucket_severity bands so frontend
// display severity matches the recalibrated backend (LOW<20, MEDIUM 20-59,
// HIGH 60-79, CRITICAL 80+).
export const severityFromScore = (score?: number | null): string | null => {
  if (score === undefined || score === null) return null
  if (score >= 80) return 'CRITICAL'
  if (score >= 60) return 'HIGH'
  if (score >= 20) return 'MEDIUM'
  return 'LOW'
}

export interface RiskDetectFields {
  issue_key?: string
  issue_keys?: string[]
  sprint_key?: string
  type: string
  hours_since_update?: number
  days_since_created?: number
  days_remaining?: number
  backlog_clear_days?: number
  burndown_gap_percent?: number
  stalled_issues?: { key: string; hours_since_update: number }[]
  overdue_issues?: { key: string; days_overdue: number }[]
}

export const riskTitle = (risk: RiskDetectFields): string => {
  if (risk.issue_key) return risk.issue_key
  if (risk.issue_keys && risk.issue_keys.length) return risk.issue_keys.join(', ')
  return risk.sprint_key || risk.type
}



export const RISK_TYPE_META: Record<string, { label: string }> = {
  STORY_NOT_PROGRESSING: { label: '📍 Not Progressing' },
  STALLED_TICKETS: { label: '🕒 Stalled Tickets' },
  BURNDOWN_BEHIND: { label: '📉 Burndown Behind' },
  QA_BOTTLENECK: { label: '🧪 QA Bottleneck' },
  EXTERNAL_DEPENDENCY: { label: '🔗 External Dep' },
  UNASSIGNED: { label: '👤 Unassigned' },
  UNESTIMATED: { label: '📐 Unestimated' },
  UNDEFINED_SCOPE: { label: '📝 Undefined Scope' },
  SIZING_RISK: { label: '🏗️ Oversized' },
  DUE_DATE_PASSED: { label: '⏰ Due Date Passed' },
  BUG_RAISED: { label: '🐛 Bug Raised' },
  SCOPE_CREEP: { label: '📈 Scope Creep' },
  SPRINT_ENDED_INCOMPLETE: { label: 'Sprint Ended Incomplete' },
  SPRINT_NOT_STARTED: { label: '🚦 Sprint Not Started' },
}

export const formatRiskType = (type: string): string => {
  return RISK_TYPE_META[type]?.label ?? type
}

const ALLOWED_INLINE_TAGS = /^(a|b|i|u|em|strong|br|ul|ol|li|p|span|code|pre)$/i

export const sanitizeInlineHtml = (html: string): string => {
  if (!html) return ''
  let clean = html.replace(/<(script|style)[\s\S]*?<\/(script|style)>/gi, '')
  clean = clean.replace(/\son\w+="[^"]*"/gi, '')
  clean = clean.replace(/\son\w+='[^']*'/gi, '')
  clean = clean.replace(/(href|src)\s*=\s*("javascript:[^"]*"|'javascript:[^']*')/gi, '$1="#"')
  clean = clean.replace(/<\/?([a-z][a-z0-9]*)\b[^>]*>/gi, (_match, tag: string) =>
    ALLOWED_INLINE_TAGS.test(tag) ? _match : '',
  )
  return clean
}

export const splitItems = (text?: string): string[] => {
  if (!text) return []
  return text.split(';').map((s) => s.trim()).filter(Boolean)
}

export const formatDate = (iso?: string, tz: string | null = displayTimezone): string => {
  if (!iso) return 'N/A'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'N/A'
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: tz || undefined })
}

export const formatLastSync = (iso?: string): string => {
  if (!iso) return 'Just now'
  const normalized = /Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return 'Just now'
  // Render in the Jira timezone so the "last synced" time matches the rest of
  // the UI (the app's events are tied to the Jira board's clock).
  return date.toLocaleTimeString(undefined, { timeZone: displayTimezone || undefined, timeZoneName: 'short' })
}

export const shortSprintName = (name?: string): string => {
  if (!name) return '?'
  const match = name.match(/(\d+)\s*$/)
  return match ? 'S' + match[1] : name.slice(0, 6)
}

const DAY_MS = 86400000

// Resolve a UTC instant to its calendar date (as UTC-midnight) in `tz`
// (defaults to the Jira timezone set via setJiraTimezone). Jira stores sprint
// end dates in UTC, so an "ending Aug 24" sprint can serialize to Aug 23
// 18:15Z and read as Aug 23 in raw UTC; interpreting it in the Jira timezone
// keeps sprints that end on the same board date aligned with the user's Jira.
const calendarMidnightUtc = (s?: string, tz: string | null = displayTimezone): number | null => {
  if (!s) return null
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return null
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz || undefined,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
    .formatToParts(d)
    .reduce<Record<string, string>>((acc, p) => {
      if (p.type !== 'literal') acc[p.type] = p.value
      return acc
    }, {})
  if (!parts.year || !parts.month || !parts.day) return null
  return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day))
}

// Calendar days a sprint is past its end date (aligns with sprintDayLabel), or
// null while the sprint is still within its dates.
export const sprintOverdueDays = (endDate?: string, tz: string | null = displayTimezone): number | null => {
  const end = calendarMidnightUtc(endDate, tz)
  if (end === null) return null
  const today = calendarMidnightUtc(new Date().toISOString(), tz)
  if (today === null || today <= end) return null
  return Math.max(1, Math.round((today - end) / DAY_MS))
}

export const sprintDayLabel = (
  startDate?: string,
  endDate?: string,
  tz: string | null = displayTimezone,
): string | null => {
  if (!startDate || !endDate) return null
  const start = calendarMidnightUtc(startDate, tz)
  const end = calendarMidnightUtc(endDate, tz)
  if (start === null || end === null) return null

  const today = calendarMidnightUtc(new Date().toISOString(), tz)
  if (today === null) return null

  // Inclusive calendar-day span.
  const totalDays = Math.max(Math.floor((end - start) / DAY_MS) + 1, 1)
  if (today > end) {
    const daysOverdue = Math.max(1, Math.round((today - end) / DAY_MS))
    return `Overdue by ${daysOverdue} day${daysOverdue === 1 ? '' : 's'}`
  }
  const elapsed = Math.floor((today - start) / DAY_MS) + 1
  const day = Math.max(1, Math.min(elapsed, totalDays))
  return `Day ${day}/${totalDays}`
}

export const describeAiFallback = (reason?: string): string => {
  switch ((reason || '').toLowerCase()) {
    case 'not_configured':
      return '🔑 No AI provider is configured. Add your Gemini/OpenRouter API key in Settings to get AI-generated plans — showing a rule-based plan built from the detected risks meanwhile.'
    case 'busy':
      return '⏳ The AI was busy with another request, so this plan came from rule-based heuristics. Try again in a moment.'
    case 'timeout':
      return "⏱️ The AI didn't respond in time — showing a rule-based plan. Retrying usually works."
    case 'auth':
      return '🔑 The AI provider rejected the request — check your API key or quota in Settings. Showing a rule-based plan.'
    default:
      return '⚠️ AI unavailable right now — showing a rule-based plan built from the detected risks.'
  }
}
