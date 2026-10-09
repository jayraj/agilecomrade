import { Link } from 'react-router-dom'
import { Activity, Rocket, TrendingUp } from 'lucide-react'

export default function EmptyDashboard() {
  return (
    <div className="dashboard-empty">
      <div className="empty-hero">
        <div className="empty-hero-content">
          <div className="empty-hero-icon">
            <Activity size={32} />
          </div>
          <h1 className="empty-hero-title">No sprint data yet</h1>
          <p className="empty-hero-text">
            Once a Jira profile is connected and a sync runs, your active sprints, upcoming work, and velocity
            trends will appear here.
          </p>
          <div className="empty-hero-actions">
            <Link className="btn-secondary" to="/settings">
              Configure a profile
            </Link>
          </div>
        </div>
      </div>

      <section className="empty-section">
        <div className="empty-section-header">
          <h2 className="empty-section-title">Active Sprint(s)</h2>
          <span className="empty-section-count">0</span>
          <span className="empty-section-status">No risks</span>
          <div className="empty-section-divider" />
        </div>
        <p className="empty-section-subtitle">
          Live risk scores for your active sprints — spot stalled tickets, burndown gaps, and fresh bugs early enough to act.
        </p>
        <div className="empty-section-card">
          <div className="empty-section-card-icon amber">
            <Rocket size={28} />
          </div>
          <h3 className="empty-section-card-title">No active sprints</h3>
          <p className="empty-section-card-text">
            No sprints are currently in progress. When a sprint starts in Jira, it will show up here with a live risk score.
          </p>
          <div className="empty-section-card-hint">Syncing pulls active sprints automatically</div>
        </div>
      </section>

      <section className="empty-section">
        <div className="empty-section-header">
          <h2 className="empty-section-title">Future Sprint(s)</h2>
          <span className="empty-section-count">0</span>
          <span className="empty-section-status">Nothing queued</span>
          <div className="empty-section-divider" />
        </div>
        <p className="empty-section-subtitle">
          A pre-planning health check — catch unassigned, unestimated, or oversized work before day one.
        </p>
        <div className="empty-section-card">
          <div className="empty-section-card-icon blue">
            <TrendingUp size={28} />
          </div>
          <h3 className="empty-section-card-title">No upcoming sprints</h3>
          <p className="empty-section-card-text">
            No future sprints are scheduled yet. Once your next sprint is planned in Jira, you'll see a pre-planning health check here.
          </p>
          <div className="empty-section-card-hint">Plan a sprint in Jira to populate this</div>
        </div>
      </section>

      <section className="empty-section">
        <div className="empty-section-header">
          <h2 className="empty-section-title">Velocity Trend</h2>
          <span className="empty-section-count">0</span>
          <span className="empty-section-status">No history</span>
          <div className="empty-section-divider" />
        </div>
        <p className="empty-section-subtitle">
          Throughput of completed sprints per project — use the trend to commit realistically in your next planning.
        </p>
        <div className="empty-section-card">
          <div className="empty-section-card-icon emerald">
            <TrendingUp size={28} />
          </div>
          <h3 className="empty-section-card-title">No velocity history</h3>
          <p className="empty-section-card-text">
            Velocity is calculated from completed sprints. After your first sprint finishes, a per-project trend chart will appear here.
          </p>
          <div className="empty-section-card-hint">Complete at least one sprint to build history</div>
        </div>
      </section>
    </div>
  )
}
