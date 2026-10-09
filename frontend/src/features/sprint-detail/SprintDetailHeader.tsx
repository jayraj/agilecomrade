import { Calendar, ChevronRight, Clock, X } from 'lucide-react'
import { formatDate } from '../../utils/format'

interface SprintDetailHeaderProps {
  sprintKey: string
  projectKey?: string
  dayLabel?: string | null
  start?: string
  end?: string
  onClose?: () => void
}

export default function SprintDetailHeader({ sprintKey, projectKey, dayLabel, start, end, onClose }: SprintDetailHeaderProps) {
  return (
    <header className="detail-shell-head">
      <div className="detail-shell-head-left">
        <div className="detail-shell-eyebrow-row">
          <span className="detail-shell-eyebrow">
            {projectKey ? `${projectKey} · ` : ''}Sprint Details
          </span>
          {dayLabel && <span className="detail-shell-day">{dayLabel}</span>}
        </div>
        <h1 className="detail-shell-title">{sprintKey}</h1>
        <div className="detail-shell-dates">
          {start && (
            <span className="detail-shell-date">
              <Calendar size={11} /> {formatDate(start)}
            </span>
          )}
          {start && end && <ChevronRight size={10} className="detail-shell-date-sep" />}
          {end && (
            <span className="detail-shell-date">
              <Clock size={11} /> {formatDate(end)}
            </span>
          )}
        </div>
      </div>
      <button type="button" className="detail-shell-close" onClick={onClose} aria-label="Close details">
        <X size={15} />
      </button>
    </header>
  )
}
