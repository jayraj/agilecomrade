import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, PlugZap, Save, X, Globe, Cpu, PenLine } from 'lucide-react'
import {
  apiConfigDefaults,
  apiCreateProfile,
  apiDeleteProfile,
  apiErrorMessage,
  apiErrorStatus,
  apiGetProfile,
  apiTestConfig,
  apiUpdateProfile,
} from '../api/client'
import { profileApi } from '../api/config'
import { clearOfflineSnapshot } from '../utils/offlineCache'

interface SettingsProps {
  onProfilesChanged: () => void
  onSelectProfile: (slug: string | null) => void
}

interface FormState {
  slug: string
  jira_cloud_url: string
  jira_email: string
  jira_api_token: string
  jira_projects: string
  llm_provider: string
  llm_model: string
  llm_api_key: string
  story_points_field: string
}

const EMPTY_FORM: FormState = {
  slug: '',
  jira_cloud_url: '',
  jira_email: '',
  jira_api_token: '',
  jira_projects: '',
  llm_provider: 'gemini',
  llm_model: '',
  llm_api_key: '',
  story_points_field: '',
}

type ValidatedKey = 'slug' | 'jira_cloud_url' | 'jira_email' | 'jira_api_token'

const isValidatedKey = (key: keyof FormState): key is ValidatedKey =>
  key === 'slug' || key === 'jira_cloud_url' || key === 'jira_email' || key === 'jira_api_token'

/** Mirrors the backend rules in main.py (SLUG_RE / JIRA_URL_RE) so invalid
 *  fields are flagged before any request leaves the browser. */
function validateForm(form: FormState, isCreate: boolean, requireToken: boolean): Partial<Record<ValidatedKey, string>> {
  const errors: Partial<Record<ValidatedKey, string>> = {}
  const slug = form.slug.trim()
  const url = form.jira_cloud_url.trim().replace(/\/+$/, '').toLowerCase()
  const email = form.jira_email.trim()
  const token = form.jira_api_token.trim()

  if (isCreate && !/^[a-z0-9][a-z0-9-]{1,39}$/.test(slug)) {
    errors.slug = 'Slug must be 2-40 chars: lowercase letters, digits, hyphens'
  }
  if (!url) {
    errors.jira_cloud_url = 'Jira Cloud URL is required'
  } else if (!/^https:\/\/[a-z0-9][a-z0-9-]*\.atlassian\.net$/.test(url)) {
    errors.jira_cloud_url = 'Must be a https://<site>.atlassian.net URL'
  }
  if (!email) {
    errors.jira_email = 'Jira email is required'
  }
  if (requireToken && !token) {
    errors.jira_api_token = 'Jira API token is required'
  }
  return errors
}

export default function Settings({ onProfilesChanged, onSelectProfile }: SettingsProps) {
  const [initialActive] = useState(() => profileApi.active())
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [accessToken, setAccessToken] = useState('')
  const [currentSlug, setCurrentSlug] = useState<string | null>(initialActive?.slug ?? null)
  const [mode, setMode] = useState<'create' | 'view' | 'edit'>(initialActive ? 'view' : 'create')
  const [defaultModels, setDefaultModels] = useState<Record<string, string>>({})
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [testResult, setTestResult] = useState<{ status: string; text: string } | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<ValidatedKey, string>>>({})

  const isView = mode === 'view'
  const isEdit = mode === 'edit'
  const isCreate = mode === 'create'
  const readOnly = isView

  const fieldErrorList = [...new Set(Object.values(fieldErrors).filter((v): v is string => Boolean(v)))]

  useEffect(() => {
    apiConfigDefaults()
      .then((d) => {
        setDefaultModels(d.default_models)
        setForm((f) => ({
          ...f,
          llm_model: f.llm_model || d.default_models[f.llm_provider] || '',
        }))
      })
      .catch(() => {})
  }, [])

  // Populate the form with the saved profile's details (async) once on mount.
  useEffect(() => {
    if (initialActive) loadIntoForm(initialActive.slug, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const refreshProfiles = () => onProfilesChanged()

  const set = (key: keyof FormState) => (value: string) => {
    setForm((f) => ({ ...f, [key]: value }))
    if (isValidatedKey(key)) {
      setFieldErrors((e) => {
        if (!e[key]) return e
        const next = { ...e }
        delete next[key]
        return next
      })
    }
  }

  const switchProvider = (provider: string) => {
    setForm((f) => ({
      ...f,
      llm_provider: provider,
      llm_model: defaultModels[provider] || '',
    }))
  }

  const loadIntoForm = async (slug: string, asEdit: boolean) => {
    setCurrentSlug(slug)
    setMessage(null)
    setFieldErrors({})
    setForm((f) => ({ ...f, slug }))
    try {
      const { profile } = await apiGetProfile(slug)
      setForm((f) => ({
        ...f,
        slug: profile.slug,
        jira_cloud_url: profile.jira_cloud_url,
        jira_email: profile.jira_email,
        jira_projects: profile.jira_projects,
        llm_provider: profile.llm_provider,
        llm_model: profile.llm_model,
        story_points_field: profile.story_points_field || '',
        jira_api_token: '',
        llm_api_key: '',
      }))
      setAccessToken('')
      setMode(asEdit ? 'edit' : 'view')
    } catch (error) {
      const status = apiErrorStatus(error)
      if (status === 401 || status === 403) {
        setMessage({
          kind: 'err',
          text: `Profile '${slug}' is not on the server anymore — fill in the form and Save to recreate it.`,
        })
      } else {
        setMessage({ kind: 'err', text: apiErrorMessage(error) })
      }
    }
  }

  const enterEdit = () => {
    if (currentSlug) setMode('edit')
  }

  const cancelEdit = () => {
    if (currentSlug) loadIntoForm(currentSlug, false)
    else {
      setForm(EMPTY_FORM)
      setAccessToken('')
      setMode('create')
    }
    setTestResult(null)
  }

  const testConnection = async () => {
    const errors = validateForm(form, isCreate, true)
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      setTestResult(null)
      return
    }
    setFieldErrors({})
    setTesting(true)
    setTestResult(null)
    try {
      const response = await apiTestConfig({
        jira_cloud_url: form.jira_cloud_url,
        jira_email: form.jira_email,
        jira_api_token: form.jira_api_token,
        jira_projects: form.jira_projects,
        llm_provider: form.llm_provider,
        llm_model: form.llm_model,
        llm_api_key: form.llm_api_key,
        story_points_field: form.story_points_field,
      })
      const r = response.result
      const lines: string[] = []
      lines.push(`Auth: ${r.auth?.ok ? '✅ ok' : `❌ ${r.auth?.error || 'failed'}`}`)
      lines.push(`Story Points field: ${r.story_points_field?.ok ? '✅ detected' : `❌ ${r.story_points_field?.error || 'not detected'}`}`)
      for (const [key, p] of Object.entries(r.projects || {})) {
        lines.push(`Project ${key}: ${p.ok ? `✅ ${p.active_sprint || 'ok'}` : `❌ ${p.error || 'failed'}`}`)
      }
      lines.push(`LLM ${r.llm?.provider} / ${r.llm?.model}: ${r.llm?.ok ? '✅ ok' : `⚠️ ${r.llm?.error || 'skipped'}`}`)
      setTestResult({ status: response.status, text: lines.join('\n') })
    } catch (error) {
      setTestResult({ status: 'error', text: apiErrorMessage(error) })
    } finally {
      setTesting(false)
    }
  }

  const saveProfile = async () => {
    const errors = validateForm(form, isCreate, isCreate)
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }
    setFieldErrors({})
    setSaving(true)
    setMessage(null)
    try {
      const token = accessToken || profileApi.generateToken()
      if (currentSlug) {
        const body: Record<string, unknown> = {
          jira_cloud_url: form.jira_cloud_url,
          jira_email: form.jira_email,
          jira_api_token: form.jira_api_token,
          jira_projects: form.jira_projects,
          llm_provider: form.llm_provider,
          llm_model: form.llm_model,
          llm_api_key: form.llm_api_key,
          story_points_field: form.story_points_field,
        }
        if (accessToken) body.access_token = accessToken
        try {
          const updateResponse = await apiUpdateProfile(currentSlug, body)
          const updatedToken = updateResponse.access_token
          if (updatedToken) {
            const list = profileApi.list().map((p) => (p.slug === currentSlug ? { ...p, token: updatedToken } : p))
            profileApi.save(list)
          }
          setMessage({ kind: 'ok', text: `Profile '${currentSlug}' updated.` })
        } catch (error) {
          const status = apiErrorStatus(error)
          if (status !== 401 && status !== 403) throw error
          // The profile row no longer exists server-side (deleted outside this
          // browser), so the auth-gated PUT can never succeed. Re-create it
          // with the same slug — which needs the Jira token back in the form.
          if (!form.jira_api_token.trim()) {
            setFieldErrors({
              jira_api_token: "Profile was removed from the server — re-enter the Jira API token to recreate it",
            })
            setMessage({
              kind: 'err',
              text: `Profile '${currentSlug}' is not on the server anymore — re-enter the Jira API token and save again.`,
            })
            return
          }
          let response: { access_token: string }
          try {
            response = await apiCreateProfile({ slug: currentSlug, access_token: token, ...body })
          } catch (createError) {
            const createStatus = apiErrorStatus(createError)
            if (createStatus !== 409) throw createError
            // Row exists but our token doesn't match it — no way to recover the
            // old token (only its hash is stored), so a new slug is required.
            setMessage({
              kind: 'err',
              text: `Profile '${currentSlug}' exists on the server but this browser's access token is no longer valid — create a profile with a different slug.`,
            })
            return
          }
          profileApi.add({ slug: currentSlug, token: response.access_token })
          setMessage({
            kind: 'ok',
            text: `Profile '${currentSlug}' was missing on the server — recreated with a new access token.`,
          })
        }
      } else {
        const response = await apiCreateProfile({
          slug: form.slug,
          access_token: token,
          jira_cloud_url: form.jira_cloud_url,
          jira_email: form.jira_email,
          jira_api_token: form.jira_api_token,
          jira_projects: form.jira_projects,
          llm_provider: form.llm_provider,
          llm_model: form.llm_model,
          llm_api_key: form.llm_api_key,
          story_points_field: form.story_points_field,
        })
        profileApi.add({ slug: form.slug, token: response.access_token })
        profileApi.setActiveSlug(form.slug)
        onSelectProfile(form.slug)
        setCurrentSlug(form.slug)
        setMessage({ kind: 'ok', text: `Profile '${form.slug}' created and activated. Access token saved in this browser.` })
      }
      setAccessToken('')
      setMode('view')
      refreshProfiles()
    } catch (error) {
      setMessage({ kind: 'err', text: apiErrorMessage(error) })
    } finally {
      setSaving(false)
    }
  }

  // Deletion is two-step and in-app (no window.confirm): the ✕ arms the
  // prompt, `confirmDeleteNow` does the work. A native dialog can return
  // false without the user knowing why, which made this look broken.
  const startDelete = () => {
    if (deleting) return
    setMessage(null)
    setConfirmDelete(true)
  }

  const cancelDelete = () => {
    if (deleting) return
    setConfirmDelete(false)
  }

  const confirmDeleteNow = async (slug: string) => {
    if (!slug || deleting) return
    setDeleting(true)
    try {
      try {
        await apiDeleteProfile(slug)
      } catch (error) {
        const status = apiErrorStatus(error)
        if (status !== 404) throw error
        // 404 means the row is already gone server-side — that's the state we
        // want, so fall through and clear the local profile as well.
        console.warn(`Profile '${slug}' was already deleted server-side`)
      }
      profileApi.remove(slug)
      void clearOfflineSnapshot(slug)
      onSelectProfile(null)
      refreshProfiles()
      setMessage({ kind: 'ok', text: `Profile '${slug}' deleted.` })
      setCurrentSlug(null)
      setForm(EMPTY_FORM)
      setAccessToken('')
      setMode('create')
      setConfirmDelete(false)
    } catch (error) {
      // profileApi.remove only runs after a successful DELETE, so local state
      // still matches the server — just show why the delete was refused.
      console.error('Failed to delete profile:', error)
      setMessage({ kind: 'err', text: apiErrorMessage(error) })
      setConfirmDelete(false)
    } finally {
      setDeleting(false)
    }
  }

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
          <div className="saved-profile-card">
          <button
            className="saved-profile-delete"
            aria-label="Delete profile"
            aria-expanded={confirmDelete}
            title="Delete profile"
            onClick={startDelete}
            disabled={deleting}
          >
            <X size={14} strokeWidth={2} />
          </button>
          <div className="saved-profile-body">
            <div className="saved-profile-avatar">
              {(currentSlug || '?').replace(/[^a-zA-Z0-9]/g, '').slice(0, 2).toUpperCase()}
            </div>
            <div className="saved-profile-info">
              <div className="saved-profile-head">
                <span className="saved-profile-slug">{currentSlug}</span>
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
                  <span>{form.llm_provider === 'gemini' ? 'Gemini' : form.llm_provider === 'openrouter' ? 'OpenRouter' : form.llm_provider} · {form.llm_model}</span>
                </div>
              </div>
              <button className="saved-profile-edit" onClick={enterEdit} disabled={deleting}>
                <PenLine size={14} strokeWidth={2} />
                Edit
              </button>
            </div>
          </div>
          {confirmDelete && currentSlug && (
            <div className="saved-profile-confirm" role="group" aria-label="Confirm delete">
              <span className="saved-profile-confirm-text">
                Delete <strong>{currentSlug}</strong>? This erases the profile and its data completely from the server.
              </span>
              <button
                type="button"
                className="saved-profile-confirm-danger"
                onClick={() => void confirmDeleteNow(currentSlug)}
                disabled={deleting}
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
              <button
                type="button"
                className="saved-profile-confirm-cancel"
                onClick={cancelDelete}
                disabled={deleting}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </>
      )}

      {!isView && (
      <div className="settings-form">
        <h3>{isEdit ? `Edit profile: ${currentSlug}` : 'New profile'}</h3>

        <div className="form-grid">
          <label className={fieldErrors.slug ? 'field-error' : undefined}>
            Slug (identifier, lowercase + hyphens)
            <input
              value={form.slug}
              onChange={(e) => set('slug')(e.target.value.toLowerCase())}
              disabled={!isCreate}
              placeholder="e.g. acme-scm"
              maxLength={40}
              aria-invalid={!!fieldErrors.slug}
            />
            {fieldErrors.slug && <span className="field-error-text">{fieldErrors.slug}</span>}
          </label>
          <label className={fieldErrors.jira_cloud_url ? 'field-error' : undefined}>
            Jira Cloud URL
            <input
              value={form.jira_cloud_url}
              onChange={(e) => set('jira_cloud_url')(e.target.value)}
              disabled={readOnly}
              placeholder="https://your-domain.atlassian.net"
              aria-invalid={!!fieldErrors.jira_cloud_url}
            />
            {fieldErrors.jira_cloud_url && (
              <span className="field-error-text">{fieldErrors.jira_cloud_url}</span>
            )}
          </label>
          <label className={fieldErrors.jira_email ? 'field-error' : undefined}>
            Jira email
            <input
              value={form.jira_email}
              onChange={(e) => set('jira_email')(e.target.value)}
              disabled={readOnly}
              placeholder="you@company.com"
              aria-invalid={!!fieldErrors.jira_email}
            />
            {fieldErrors.jira_email && <span className="field-error-text">{fieldErrors.jira_email}</span>}
          </label>
          <label className={fieldErrors.jira_api_token ? 'field-error' : undefined}>
            Jira API token {isEdit && <em>(blank = keep current)</em>}
            <input
              value={form.jira_api_token}
              onChange={(e) => set('jira_api_token')(e.target.value)}
              disabled={readOnly}
              placeholder="ATATT3xFfGF..."
              type="password"
              autoComplete="off"
              aria-invalid={!!fieldErrors.jira_api_token}
            />
            {fieldErrors.jira_api_token && (
              <span className="field-error-text">{fieldErrors.jira_api_token}</span>
            )}
          </label>
          <label>
            Project keys (comma separated)
            <input
              value={form.jira_projects}
              onChange={(e) => set('jira_projects')(e.target.value)}
              disabled={readOnly}
              placeholder="PFIN, MOS"
            />
          </label>
          <label>
            Story points field (optional — blank auto-detects)
            <input
              value={form.story_points_field}
              onChange={(e) => set('story_points_field')(e.target.value)}
              disabled={readOnly}
              placeholder="customfield_10102"
            />
          </label>
        </div>

        <div className="form-grid">
          <label>
            LLM provider <em>(optional — skip for rule-based only)</em>
            <select value={form.llm_provider} onChange={(e) => switchProvider(e.target.value)} disabled={readOnly}>
              <option value="gemini">Gemini</option>
              <option value="openrouter">OpenRouter</option>
            </select>
          </label>
          <label>
            Model
            <input
              value={form.llm_model}
              onChange={(e) => set('llm_model')(e.target.value)}
              disabled={readOnly}
              placeholder={defaultModels[form.llm_provider] || 'gemini-flash-latest'}
            />
          </label>
          <label className="form-full">
            LLM API key {isEdit ? <em>(blank = keep current)</em> : <em>(optional — blank disables AI analysis)</em>}
            <input
              value={form.llm_api_key}
              onChange={(e) => set('llm_api_key')(e.target.value)}
              disabled={readOnly}
              type="password"
              autoComplete="off"
              placeholder={form.llm_provider === 'gemini' ? 'AIza...' : 'sk-or-v1-...'}
            />
          </label>
        </div>

        {(fieldErrorList.length > 0 || message?.kind === 'err') && (
          <div className="form-error-banner" role="alert">
            {fieldErrorList.length > 0
              ? fieldErrorList.map((m) => <span key={m}>{m}</span>)
              : <span>{message?.text}</span>}
          </div>
        )}

        <div className="form-actions">
          {!isView && (
            <button className="settings-btn" onClick={testConnection} disabled={testing || saving}>
              <PlugZap size={16} strokeWidth={2} />
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
          )}
          {!isView && (
            <button className="settings-btn-primary" onClick={saveProfile} disabled={saving || testing}>
              <Save size={16} strokeWidth={2} />
              {saving ? 'Saving...' : 'Save'}
            </button>
          )}
          {isEdit && (
            <button className="settings-btn-danger" onClick={cancelEdit}>
              <X size={16} strokeWidth={2} />
              Cancel
            </button>
          )}
        </div>

        <div className="token-note privacy-note">
          <p>
            <strong>Test Connection</strong> checks your Jira and LLM settings without saving any data.
          </p>
          <p>
            <strong>Save</strong> securely saves the profile. Your Jira token and LLM API key are encrypted. A browser access token is created and used to keep your requests secure.
          </p>
          <p>
            See the{' '}
            <a href="/privacy.html" target="_blank" rel="noreferrer">Privacy Policy</a> to learn how
            your data is stored, used, and shared with the LLM during AI features.
          </p>
        </div>

        {testResult && (
          <div className={`test-result ${testResult.status === 'ok' ? 'ok' : 'partial'}`}>
            <pre>{testResult.text}</pre>
          </div>
        )}
      </div>
      )}
    </div>
  )
}
