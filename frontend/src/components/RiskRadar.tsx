import { Link } from 'react-router-dom'
import { RefreshCw, TriangleAlert } from 'lucide-react'
import { useSnapshot } from '../hooks/useSnapshot'
import { useSync } from '../context/SyncContext'
import SprintCard from './SprintCard'
import SectionHeader from './SectionHeader'
import type { Blocker } from '../api/client'

interface RiskRadarProps {
  onSelectDetail?: (selection: { kind: 'active' | 'future'; key: string }) => void
}

export default function RiskRadar({ onSelectDetail }: RiskRadarProps) {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, error, noProfile } = useSnapshot(syncIntervalSeconds, refreshKey)

  const radarData = snapshot?.radar_data ?? []
  const blockers = snapshot?.blockers ?? []

  const hasRisks = radarData.some((r) => (r.risk_types?.length ?? 0) > 0)

  if (noProfile) {
    return (
      <div className="no-data">
        <p>No profile configured yet.</p>
        <Link className="details-btn" to="/settings">⚙️ Go to Settings to create a profile</Link>
      </div>
    )
  }

  if (error) {
    return <div className="loading">⚠️ {error} — check Settings → profile configuration.</div>
  }

  return (
    <div className="risk-radar">
      <SectionHeader
        title="Active Sprint(s) Health"
        count={radarData.length}
        status={{ label: `${snapshot?.total_risks ?? 0} risks`, tone: 'amber' }}
      />
      <p className="component-subtitle">Live risk scores for your active sprints — spot stalled tickets, burndown gaps, and fresh bugs early enough to act.</p>
      {radarData.length > 0 && (
        <div className="portfolio-meta">
          {new Set(radarData.map((r) => r.project_key).filter(Boolean)).size} projects ·{' '}
          {radarData.length} teams · {snapshot?.total_risks ?? 0} risks flagged
        </div>
      )}

      {loading ? (
        <div className="no-data no-data-soft loading-callout">
          <RefreshCw size={16} className="spin" /> Loading active sprint data...
        </div>
      ) : (
        <>
          {radarData.length > 0 && !hasRisks && (
            <div className="smooth-banner">
              Hey there, everything is on track with the current sprints. Please continue monitoring for potential risks, address them proactively, and ensure timely follow-up on any mitigation actions.
            </div>
          )}
          {radarData.length > 0 && hasRisks && (
            <div className="risk-warning-banner">
              <TriangleAlert size={16} className="risk-warning-icon" />
              <span>Attention, one of your sprints is at RISK. Do a proactive assessment!</span>
            </div>
          )}

          <div className="sprint-card-grid">
            {radarData.map((risk) => {
              const cardBlockers: Blocker[] = blockers.filter((b) => b.sprint_key === risk.sprint_key)
              return (
                <SprintCard
                  key={risk.sprint_key}
                  data={risk}
                  blockers={cardBlockers}
                  onDetails={onSelectDetail ? () => onSelectDetail({ kind: 'active', key: risk.sprint_key }) : undefined}
                />
              )
            })}
          </div>
        </>
      )}

      {radarData.length === 0 && !loading && (
        <div className="no-data no-data-soft">
          ✅ There are currently no active sprints running in Jira. Once you start a sprint, it will appear here.
        </div>
      )}
    </div>
  )
}
