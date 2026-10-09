import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="empty-profile">
      <div className="empty-profile-icon" aria-hidden="true">
        🔍
      </div>
      <h1 className="empty-profile-title">Page not found</h1>
      <p className="empty-profile-text">
        The page you were looking for doesn&rsquo;t exist or has moved.
      </p>
      <Link to="/" className="ai-scan-btn">
        Go to Dashboard
      </Link>
    </div>
  )
}