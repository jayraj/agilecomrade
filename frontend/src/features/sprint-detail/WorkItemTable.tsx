interface WorkItem {
  key?: string
  summary?: string
  status?: string
  assignee?: string
  story_points?: number
}

function initials(name?: string): string {
  if (!name) return '—'
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '—'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export default function WorkItemTable({
  items,
  riskSeverityByKey,
}: {
  items: WorkItem[]
  riskSeverityByKey?: Map<string, string>
}) {
  return (
    <div className="work-item-list">
      <div className="work-item-row work-item-head">
        <span className="work-item-cell">Ticket #</span>
        <span className="work-item-cell">Summary</span>
        <span className="work-item-cell">Status</span>
        <span className="work-item-cell">SP</span>
        <span className="work-item-cell">Assignee</span>
      </div>
      {items.map((it, i) => {
        const sev = it.key ? riskSeverityByKey?.get(it.key) : undefined
        return (
          <div className="work-item-row" key={it.key || i}>
            <span className="work-item-cell work-item-key">{it.key}</span>
            <span className="work-item-cell work-item-summary">{it.summary}</span>
            <span className="work-item-cell work-item-status">
              {sev ? (
                <span className={`status-chip status-chip--${sev.toLowerCase()}`}>{it.status || '—'}</span>
              ) : (
                it.status || '—'
              )}
            </span>
            <span className="work-item-cell work-item-sp">{it.story_points ?? '—'}</span>
            <span className="work-item-cell work-item-assignee">{it.assignee ? initials(it.assignee) : '—'}</span>
          </div>
        )
      })}
    </div>
  )
}
