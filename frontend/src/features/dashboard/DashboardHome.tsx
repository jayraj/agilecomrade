import RiskRadar from './RiskRadar'
import VelocityTrend from './VelocityTrend'
import NextSprintOverview from './NextSprintOverview'
import EmptyDashboard from './EmptyDashboard'
import { useSnapshot } from '../../hooks/useSnapshot'
import { useSync } from '../../context/SyncContext'

interface DashboardHomeProps {
  onSelectDetail?: (selection: { kind: 'active' | 'future'; key: string }) => void
}

export default function DashboardHome({ onSelectDetail }: DashboardHomeProps) {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { noProfile } = useSnapshot(syncIntervalSeconds, refreshKey)

  if (noProfile) {
    return <EmptyDashboard />
  }

  return (
    <div className="dashboard-home">
      <RiskRadar onSelectDetail={onSelectDetail} />
      <NextSprintOverview onSelectDetail={onSelectDetail} />
      <VelocityTrend />
    </div>
  )
}
