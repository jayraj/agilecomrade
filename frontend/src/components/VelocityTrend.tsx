import EmptyDashboard from './EmptyDashboard'
import { useEffect } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  type ChartData,
  type ChartOptions,
  type TooltipItem,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { useSnapshot } from '../hooks/useSnapshot'
import { useSync } from '../context/SyncContext'
import SectionHeader from './SectionHeader'
import type { VelocityData, VelocitySprint } from '../api/client'
import { shortSprintName } from '../utils/format'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip)

interface VelocityTrendProps {
  /** Pass snapshot velocity directly to skip reading it from the shared poll again. */
  velocity?: VelocityData
}

export default function VelocityTrend({ velocity }: VelocityTrendProps) {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, noProfile } = useSnapshot(syncIntervalSeconds, refreshKey)

  if (noProfile) {
    return <EmptyDashboard />
  }

  const data = velocity ?? snapshot?.velocity ?? {}

  // Some mobile browsers don't fire a resize after orientation change,
  // leaving Chart.js canvases at their pre-rotation width.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const trigger = () => {
      clearTimeout(timer)
      // Re-dispatch once the new viewport dimensions have settled.
      window.dispatchEvent(new Event('resize'))
      timer = setTimeout(() => window.dispatchEvent(new Event('resize')), 250)
    }
    window.addEventListener('orientationchange', trigger)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('orientationchange', trigger)
    }
  }, [])

  const averageVelocity = (sprints: VelocitySprint[]) => {
    if (!sprints || sprints.length === 0) return 0
    const sum = sprints.reduce((acc, s) => acc + (s.completed_sp || 0), 0)
    return (sum / sprints.length).toFixed(1)
  }

  const chartData = (sprints: VelocitySprint[]): ChartData<'line'> => {
    const colors = ['#667eea', '#764ba2', '#059669', '#ea8c00', '#ef4444']
    return {
      labels: sprints.map((s) => shortSprintName(s.sprint_key)),
      datasets: [
        {
          label: 'Completed SP',
          data: sprints.map((s) => s.completed_sp || 0),
          borderColor: colors[0],
          backgroundColor: sprints.map((_, i) => colors[i % colors.length]),
          pointBackgroundColor: sprints.map((_, i) => colors[i % colors.length]),
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 4,
          fill: false,
        },
      ],
    }
  }

  const chartOptions = (projectKey: string): ChartOptions<'line'> => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx: TooltipItem<'line'>) => {
            const sprint = (data[projectKey] || []).find(
              (s) => shortSprintName(s.sprint_key) === ctx.label,
            )
            if (sprint) {
              return ` ${sprint.completed_sp} SP completed of ${sprint.total_sp} (${sprint.completed_percent}%)`
            }
            return ` ${ctx.parsed.y} SP`
          },
        },
      },
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: { precision: 0 },
        grid: { color: '#e4e4e7' },
      },
      x: {
        grid: { display: false },
      },
    },
  })

  const projectKeys = Object.keys(data)
  const allSprints = projectKeys.flatMap((k) => data[k] || [])
  const overallAvg =
    allSprints.length > 0
      ? (allSprints.reduce((acc, s) => acc + (s.completed_sp || 0), 0) / allSprints.length).toFixed(1)
      : '0'

  return (
    <div className="next-sprint-overview">
      <SectionHeader
        title="Velocity Trend"
        count={projectKeys.length}
        status={projectKeys.length > 0 ? { label: `avg ${overallAvg} SP`, tone: 'green' } : undefined}
      />
      <p className="component-subtitle">Throughput of completed sprints per project — use the trend to commit realistically in your next planning.</p>
      {loading ? (
        <div className="no-data no-data-soft loading-callout">
          <RefreshCw size={16} className="spin" /> Loading velocity data...
        </div>
      ) : Object.keys(data).length === 0 ? (
        <div className="no-data no-data-soft">✅ No completed sprint data available yet.</div>
      ) : (
        <>
          <div className="velocity-project-avg">
            {Object.entries(data).map(([projectKey, sprints]) => (
              <span key={projectKey} className="avg-pill">
                {projectKey} · avg {averageVelocity(sprints)} SP
              </span>
            ))}
          </div>
          <div className="velocity-grid">
            {Object.entries(data).map(([projectKey, sprints]) => (
              <div key={projectKey} className="velocity-project">
                <div className="sprint-card-head">
                  <span className="sprint-card-eyebrow">VELOCITY</span>
                  <div className="sprint-card-head-row">
                    <span className="sprint-card-name">{projectKey}</span>
                  </div>
                </div>
                <div className="bar-chart-wrap">
                  <Line data={chartData(sprints)} options={chartOptions(projectKey)} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
