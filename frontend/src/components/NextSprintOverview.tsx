import { Link } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'
import { useSnapshot } from '../hooks/useSnapshot'
import { useSync } from '../context/SyncContext'
import SprintCard from './SprintCard'
import SectionHeader from './SectionHeader'
import { formatDate } from '../utils/format'

interface NextSprintOverviewProps {
  onSelectDetail?: (selection: { kind: 'active' | 'future'; key: string }) => void
}

export default function NextSprintOverview({ onSelectDetail }: NextSprintOverviewProps) {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, error, noProfile } = useSnapshot(syncIntervalSeconds, refreshKey)
  const projects = snapshot?.next_sprint_overview.projects ?? []

  if (noProfile) {
    return (
      <div className="no-data">
        <p>No profile configured yet.</p>
        <Link className="details-btn" to="/settings">⚙️ Go to Settings to create a profile</Link>
      </div>
    )
  }

  if (error) {
    return <div className="loading">⚠️ {error}</div>
  }

  return (
    <div className="next-sprint-overview">
      <SectionHeader
        title="Future Sprint(s) Readiness"
        count={projects.length}
        status={
          projects.length > 0
            ? {
                label: `Starts ${formatDate(
                  projects
                    .map((p) => p.start_date)
                    .filter(Boolean)
                    .sort()[0] as string,
                )}`,
                tone: 'green',
              }
            : undefined
        }
      />
      <p className="component-subtitle">A pre-planning health check — catch unassigned, unestimated, or oversized work before day one.</p>

      {loading ? (
        <div className="no-data no-data-soft loading-callout">
          <RefreshCw size={16} className="spin" /> Loading upcoming sprint data...
        </div>
      ) : (
        <div className="sprint-card-grid">
          {projects.map((project) => (
            <SprintCard
              key={project.project_key}
              data={project}
              eyebrow="UPCOMING SPRINT"
              progressMode="atRisk"
              detailsTo={`/future/${encodeURIComponent(project.project_key)}`}
              onDetails={onSelectDetail ? () => onSelectDetail({ kind: 'future', key: project.project_key }) : undefined}
            />
          ))}
        </div>
      )}

      {!loading && projects.length === 0 && (
        <div className="no-data no-data-soft">
          ✅ No upcoming sprints in Jira. Follow N+1 planning to identify risks early.
        </div>
      )}
    </div>
  )
}
