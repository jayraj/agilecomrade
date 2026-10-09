import { Cpu, Globe, PenLine, X } from 'lucide-react'
import type { FormState } from './settingsForm'

interface SavedProfileCardProps {
  slug: string
  form: FormState
  confirmDelete: boolean
  deleting: boolean
  onStartDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
  onEdit: () => void
}

export default function SavedProfileCard({
  slug,
  form,
  confirmDelete,
  deleting,
  onStartDelete,
  onCancelDelete,
  onConfirmDelete,
  onEdit,
}: SavedProfileCardProps) {
  return (
    <div className="saved-profile-card">
      <button
        type="button"
        className="saved-profile-delete"
        aria-label="Delete profile"
        aria-expanded={confirmDelete}
        title="Delete profile"
        onClick={onStartDelete}
        disabled={deleting}
      >
        <X size={14} strokeWidth={2} />
      </button>
      <div className="saved-profile-body">
        <div className="saved-profile-avatar">
          {(slug || '?').replace(/[^a-zA-Z0-9]/g, '').slice(0, 2).toUpperCase()}
        </div>
        <div className="saved-profile-info">
          <div className="saved-profile-head">
            <span className="saved-profile-slug">{slug}</span>
            <span className="saved-profile-active">Active</span>
          </div>
          <span className="saved-profile-email">{form.jira_email}</span>
          <div className="saved-profile-meta">
            <div className="saved-profile-meta-row">
              <Globe size={14} strokeWidth={2} />
              <span>{form.jira_cloud_url}</span>
            </div>
            <div className="saved-profile-meta-row">
              <Cpu size={14} strokeWidth={2} />
              <span>
                {form.llm_provider === 'gemini' ? 'Gemini' : form.llm_provider === 'openrouter' ? 'OpenRouter' : form.llm_provider} · {form.llm_model}
              </span>
            </div>
          </div>
          <button type="button" className="saved-profile-edit" onClick={onEdit} disabled={deleting}>
            <PenLine size={14} strokeWidth={2} />
            Edit
          </button>
        </div>
      </div>
      {confirmDelete && (
        <div className="saved-profile-confirm" role="group" aria-label="Confirm delete">
          <span className="saved-profile-confirm-text">
            Delete <strong>{slug}</strong>? This erases the profile and its data completely from the server.
          </span>
          <button
            type="button"
            className="saved-profile-confirm-danger"
            onClick={onConfirmDelete}
            disabled={deleting}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
          <button
            type="button"
            className="saved-profile-confirm-cancel"
            onClick={onCancelDelete}
            disabled={deleting}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
