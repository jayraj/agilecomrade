import { Link } from 'react-router-dom'
import { Zap, RefreshCw, Settings, WifiOff } from 'lucide-react'

interface TopStripProps {
  lastSync: string
  syncing: boolean
  offline: boolean
  onSyncNow: () => void
  activeProfile: string | null
}

export default function TopStrip({
  lastSync,
  syncing,
  offline,
  onSyncNow,
  activeProfile,
}: TopStripProps) {
  return (
    <header className="top-strip">
      <Link to="/" className="strip-brand" aria-label="Go to home">
        <span className="strip-brand-logo">
          <Zap size={13} className="strip-brand-logo-icon" />
        </span>
        <span className="strip-brand-text">
          <span className="strip-brand-name">Agile Comrade</span>
          <span className="strip-brand-tagline">Your sprint companion</span>
        </span>
      </Link>

      <div className="top-strip-actions">
        {offline && (
          <span className="offline-badge" role="status">
            <WifiOff size={12} />
            Offline
          </span>
        )}
        <span className="last-sync">
          {offline ? 'Showing data from' : 'Last sync:'}{' '}
          <span className="last-sync-time">{lastSync}</span>
        </span>

        <button
          type="button"
          onClick={onSyncNow}
          className="strip-sync-btn"
          disabled={syncing || !activeProfile || offline}
          title={offline ? 'You are offline — reconnecting will sync automatically' : undefined}
        >
          <RefreshCw size={14} className={syncing ? 'spin strip-sync-icon' : 'strip-sync-icon'} />
          <span className="strip-sync-label">{syncing ? 'Syncing...' : 'Sync Now'}</span>
        </button>

        <Link to="/settings" className="strip-settings-btn" aria-label="Settings" title="Settings">
          <Settings size={16} />
        </Link>
      </div>
    </header>
  )
}