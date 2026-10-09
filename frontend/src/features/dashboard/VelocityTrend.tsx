import EmptyDashboard from './EmptyDashboard'
import { useEffect, useMemo } from 'react'
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
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import { useSnapshot } from '../../hooks/useSnapshot'
import { useSync } from '../../context/SyncContext'
import SectionHeader from '../../components/SectionHeader'
import type { VelocitySprint } from '../../api'
import { shortSprintName } from '../../utils/format'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip)

const EMPTY_VELOCITY: Record<string, VelocitySprint[]> = {}

// Chart.js draws on a canvas and cannot resolve CSS custom properties, so we
// read the design tokens off the document root at runtime.
const readToken = (name: string, fallback: string): string => {
  if (typeof window === 'undefined') return fallback
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

export default function VelocityTrend() {
  const { syncIntervalSeconds, refreshKey } = useSync()
  const { snapshot, loading, noProfile } = useSnapshot(syncIntervalSeconds, refreshKey)

  // Some mobile browsers don't fire a resize after orientation change,
  // leaving Chart.js canvases at their pre-rotation width.
  // Declared before the noProfile early return: hook order must not change
  // when a profile appears or disappears (deleting one flips noProfile live).
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

  const data = snapshot?.velocity ?? EMPTY_VELOCITY

  const palette = useMemo(
    () => [
      readToken('--color-primary-500', '#3b82f6'),
      readToken('--color-primary-800', '#1e40af'),
      readToken('--color-success', '#10b981'),
      readToken('--color-warning', '#f59e0b'),
      readToken('--color-error', '#ef4444'),
    ],
    [],
  )
  const gridColor = useMemo(() => readToken('--border-color', '#e4e4e7'), [])

  const chartDataByProject = useMemo(() => {
    const out: Record<string, ChartData<'line'>> = {}
    for (const [projectKey, sprints] of Object.entries(data)) {
      out[projectKey] = {
        labels: sprints.map((s) => shortSprintName(s.sprint_key)),
        datasets: [
          {
            label: 'Completed SP',
            data: sprints.map((s) => s.completed_sp || 0),
            borderColor: palette[0],
            backgroundColor: sprints.map((_, i) => palette[i % palette.length]),
            pointBackgroundColor: sprints.map((_, i) => palette[i % palette.length]),
            borderWidth: 2,
            tension: 0.3,
            pointRadius: 4,
            fill: false,
          },
        ],
      }
    }
    return out
  }, [data, palette])

  const chartOptionsByProject = useMemo(() => {
    const out: Record<string, ChartOptions<'line'>> = {}
    for (const [projectKey, sprints] of Object.entries(data)) {
      out[projectKey] = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const sprint = sprints.find((s) => shortSprintName(s.sprint_key) === ctx.label)
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
            grid: { color: gridColor },
          },
          x: {
            grid: { display: false },
          },
        },
      }
    }
    return out
  }, [data, gridColor])

  if (noProfile) {
    return <EmptyDashboard />
  }

  const averageVelocity = (sprints: VelocitySprint[]) => {
    if (!sprints || sprints.length === 0) return 0
    const sum = sprints.reduce((acc, s) => acc + (s.completed_sp || 0), 0)
    return (sum / sprints.length).toFixed(1)
  }

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
      ) : projectKeys.length === 0 ? (
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
            {Object.entries(data).map(([projectKey]) => (
              <div key={projectKey} className="velocity-project">
                <div className="sprint-card-head">
                  <span className="sprint-card-eyebrow">VELOCITY</span>
                  <div className="sprint-card-head-row">
                    <span className="sprint-card-name">{projectKey}</span>
                  </div>
                </div>
                <div className="bar-chart-wrap">
                  <Line data={chartDataByProject[projectKey]} options={chartOptionsByProject[projectKey]} />
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
