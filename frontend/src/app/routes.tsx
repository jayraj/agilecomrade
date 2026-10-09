import { Link, Route, Routes, useParams } from 'react-router-dom'
import DashboardHome from '../features/dashboard/DashboardHome'
import SprintDetailPanel from '../features/sprint-detail/SprintDetailPanel'
import Settings from '../features/settings/Settings'
import NotFound from '../components/NotFound'
import type { DetailSelection } from '../features/sprint-detail/DetailSidebar'

interface AppRoutesProps {
  hasProfile: boolean
  onSelectDetail: (selection: DetailSelection) => void
  onSelectProfile: (slug: string | null) => void
}

// Route wrappers so useParams() is called at the top level of a component
// rather than from inside JSX (which violates the Rules of Hooks).
function ActiveSprintRoute() {
  const { sprintKey } = useParams()
  return <SprintDetailPanel kind="active" sprintKey={decodeURIComponent(sprintKey ?? '')} />
}

function FutureSprintRoute() {
  const { projectKey } = useParams()
  return <SprintDetailPanel kind="future" sprintKey={decodeURIComponent(projectKey ?? '')} />
}

function EmptyProfileState() {
  return (
    <div className="empty-profile">
      <div className="empty-profile-icon">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="6" />
          <circle cx="12" cy="12" r="2" />
        </svg>
      </div>
      <h2 className="empty-profile-title">No profile configured yet</h2>
      <p className="empty-profile-text">
        Connect your Jira Cloud account to start tracking sprint risks
        across current and future sprints.
      </p>
      <Link className="ai-scan-btn empty-profile-cta" to="/settings">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 12h14" />
          <path d="M12 5v14" />
        </svg>
        Create a Profile
      </Link>
    </div>
  )
}

export default function AppRoutes({
  hasProfile,
  onSelectDetail,
  onSelectProfile,
}: AppRoutesProps) {
  return (
    <Routes>
      <Route
        path="/"
        element={hasProfile ? <DashboardHome onSelectDetail={onSelectDetail} /> : <EmptyProfileState />}
      />
      <Route path="/sprint/:sprintKey" element={<ActiveSprintRoute />} />
      <Route path="/future/:projectKey" element={<FutureSprintRoute />} />
      <Route
        path="/settings"
        element={<Settings onSelectProfile={onSelectProfile} />}
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
