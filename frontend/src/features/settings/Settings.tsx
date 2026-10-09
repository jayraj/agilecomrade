import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import SavedProfileCard from './SavedProfileCard'
import ProfileForm from './ProfileForm'
import { useProfileSettings } from './useProfileSettings'

interface SettingsProps {
  onProfilesChanged: () => void
  onSelectProfile: (slug: string | null) => void
}

export default function Settings({ onProfilesChanged, onSelectProfile }: SettingsProps) {
  const {
    form,
    currentSlug,
    confirmDelete,
    deleting,
    isCreate,
    isView,
    isEdit,
    readOnly,
    defaultModels,
    fieldErrors,
    fieldErrorList,
    message,
    testing,
    saving,
    testResult,
    set,
    switchProvider,
    enterEdit,
    cancelEdit,
    testConnection,
    saveProfile,
    startDelete,
    cancelDelete,
    confirmDeleteNow,
  } = useProfileSettings(onProfilesChanged, onSelectProfile)

  return (
    <div className="settings-page">
      <Link to="/" className="settings-breadcrumb" aria-label="Navigation">
        <ArrowLeft size={16} strokeWidth={2} />
        Go to Dashboard
      </Link>
      <h2 className="settings-title">Settings</h2>
      <p className="settings-intro">
        Configure your Jira Cloud workspace and LLM provider. Your profile is stored encrypted in Supabase; the access
        token is kept only in this browser and validated as a hash by the backend.
      </p>

      <p className="token-note settings-guide-link">
        New to Agile Comrade?{' '}
        <a href="/user-guide.html" target="_blank" rel="noreferrer">Read the User Guide →</a>
      </p>

      {isCreate && (
        <div className="no-data">
          <p>No profile saved in this browser yet. Create your first one below.</p>
        </div>
      )}

      {isCreate && message?.kind === 'ok' && (
        <div className={`form-message ${message.kind}`}>{message.text}</div>
      )}

      {currentSlug && (
        <>
          <h3 className="saved-profile-heading">Saved profile (this browser)</h3>
          {message?.kind === 'ok' && (
            <div className={`form-message ${message.kind}`}>{message.text}</div>
          )}
          <SavedProfileCard
            slug={currentSlug}
            form={form}
            confirmDelete={confirmDelete}
            deleting={deleting}
            onStartDelete={startDelete}
            onCancelDelete={cancelDelete}
            onConfirmDelete={() => void confirmDeleteNow(currentSlug)}
            onEdit={enterEdit}
          />
        </>
      )}

      {!isView && (
        <ProfileForm
          form={form}
          currentSlug={currentSlug}
          isCreate={isCreate}
          isEdit={isEdit}
          isView={isView}
          readOnly={readOnly}
          defaultModels={defaultModels}
          fieldErrors={fieldErrors}
          fieldErrorList={fieldErrorList}
          message={message}
          testing={testing}
          saving={saving}
          testResult={testResult}
          onFieldChange={set}
          onSwitchProvider={switchProvider}
          onTestConnection={testConnection}
          onSave={saveProfile}
          onCancelEdit={cancelEdit}
        />
      )}
    </div>
  )
}
