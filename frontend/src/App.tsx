import { useEffect, useState } from 'react'
import { Routes, Route, Link, useLocation, useParams } from 'react-router-dom'
import { MessageSquare } from 'lucide-react'
import TopStrip from './components/TopStrip'
import DashboardHome from './components/DashboardHome'
import DetailSidebar, { type DetailSelection } from './components/DetailSidebar'
import SprintDetailPanel from './components/SprintDetailPanel'
import Settings from './components/Settings'
import { apiSyncNow, FEEDBACK_URL } from './api/client'
import { profileApi } from './api/config'
import { subscribeLastSync, useSnapshot } from './hooks/useSnapshot'
import { SyncContext } from './context/SyncContext'
import { formatLastSync } from './utils/format'

export default function App() {
  const [profiles, setProfiles] = useState(() => profileApi.list())
  const [activeProfile, setActiveProfile] = useState(() => profileApi.activeSlug())
  const [lastSync, setLastSync] = useState('Never')
  const [syncing, setSyncing] = useState(false)
  const [syncIntervalSeconds] = useState(300)
  const [refreshKey, setRefreshKey] = useState(0)
  const [detail, setDetail] = useState<DetailSelection | null>(null)
  const [disclaimerDismissed, setDisclaimerDismissed] = useState(
    () => localStorage.getItem('srr_disclaimer_dismissed') === '1'
  )
  const location = useLocation()
  const detailOpen = detail !== null && location.pathname === '/'
  const { offline } = useSnapshot(syncIntervalSeconds, refreshKey)

  const dismissDisclaimer = () => {
    localStorage.setItem('srr_disclaimer_dismissed', '1')
    setDisclaimerDismissed(true)
  }

  useEffect(() => {
    const unsubscribe = subscribeLastSync((value) => {
      if (value) setLastSync(formatLastSync(value))
    })
    return unsubscribe
  }, [])

  const refreshProfiles = () => setProfiles(profileApi.list())

  const handleSelectProfile = (slug: string) => {
    profileApi.setActiveSlug(slug)
    setActiveProfile(slug)
    setRefreshKey((k) => k + 1)
  }

  const syncNow = async () => {
    setSyncing(true)
    try {
      const response = await apiSyncNow()
      setLastSync(formatLastSync(response.last_sync))
      setRefreshKey((k) => k + 1)
      alert('Sync completed!')
    } catch (error) {
      console.error('Error syncing:', error)
      alert('Sync failed. Check the profile configuration.')
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className={`app-container${detailOpen ? ' with-sidebar' : ''}`}>
      <TopStrip
        lastSync={lastSync}
        syncing={syncing}
        offline={offline}
        onSyncNow={syncNow}
        profiles={profiles}
        activeProfile={activeProfile}
      />

      {!disclaimerDismissed && (
        <div className="disclaimer-banner" role="note">
          <span>
            ⚠️ MVP demo — sprint data (including assignee names &amp; issue text) is sent to third-party AI
            (Gemini/OpenRouter) for analysis. Avoid connecting sensitive or production Jira workspaces.{' '}
            <a href="/privacy.html" target="_blank" rel="noreferrer">Learn more →</a>
          </span>
          <button
            className="disclaimer-dismiss"
            onClick={dismissDisclaimer}
            aria-label="Dismiss disclaimer"
          >
            ✕
          </button>
        </div>
      )}

      <SyncContext.Provider value={{ syncIntervalSeconds, refreshKey }}>
      <div className="app-body">
        <main className="app-main">
          <Routes>
            <Route
              path="/"
              element={
                !activeProfile ? (
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
                ) : (
                  <DashboardHome onSelectDetail={setDetail} />
                )
              }
            />
            <Route
              path="/sprint/:sprintKey"
              element={
                <SprintDetailPanel
                  kind="active"
                  sprintKey={decodeURIComponent(useParams().sprintKey ?? '')}
                />
              }
            />
            <Route
              path="/future/:projectKey"
              element={
                <SprintDetailPanel
                  kind="future"
                  sprintKey={decodeURIComponent(useParams().projectKey ?? '')}
                />
              }
            />
            <Route
              path="/settings"
              element={<Settings onProfilesChanged={refreshProfiles} onSelectProfile={handleSelectProfile} />}
            />
          </Routes>
        </main>

        {detailOpen && (
          <>
            <div className="sidebar-backdrop" onClick={() => setDetail(null)} aria-hidden="true" />
            <DetailSidebar selection={detail} onClose={() => setDetail(null)} />
          </>
        )}
      </div>
      </SyncContext.Provider>

      <footer className="app-footer">
        {FEEDBACK_URL && (
          <a
            href={FEEDBACK_URL}
            target="_blank"
            rel="noreferrer"
            className="footer-feedback"
            title="Please leave your valuable feedback here"
          >
            <MessageSquare size={14} className="footer-feedback-icon" strokeWidth={2} />
            <span>Please leave your valuable feedback here.</span>
          </a>
        )}
      </footer>
    </div>
  )
}