import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { MessageSquare } from 'lucide-react'
import TopStrip from './components/TopStrip'
import DetailSidebar, { type DetailSelection } from './features/sprint-detail/DetailSidebar'
import AppRoutes from './app/routes'
import AppProviders from './app/providers'
import { apiSyncNow, FEEDBACK_URL, profileApi } from './api'
import { subscribeLastSync, useSnapshot } from './hooks/useSnapshot'
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

  // `null` clears the active profile (used after a delete) so the home route
  // falls back to the "No profile configured yet" empty state instead of
  // rendering a dashboard for a profile that no longer exists.
  const handleSelectProfile = (slug: string | null) => {
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
            ⚠️ MVP demo — this app stores your sprint data (including assignee names &amp; issue text) on
            the server, and sends it to third-party AI (Gemini/OpenRouter) for analysis only when you use an
            AI feature (Mitigate / Scan / Draft). Never in normal sync. Avoid connecting sensitive or
            production Jira workspaces.{' '}
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

      <AppProviders syncIntervalSeconds={syncIntervalSeconds} refreshKey={refreshKey}>
        <div className="app-body">
          <main className="app-main">
            <AppRoutes
              hasProfile={!!activeProfile}
              onSelectDetail={setDetail}
              onProfilesChanged={refreshProfiles}
              onSelectProfile={handleSelectProfile}
            />
          </main>

          {detailOpen && (
            <>
              <div className="sidebar-backdrop" onClick={() => setDetail(null)} aria-hidden="true" />
              <DetailSidebar selection={detail} onClose={() => setDetail(null)} />
            </>
          )}
        </div>
      </AppProviders>

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
