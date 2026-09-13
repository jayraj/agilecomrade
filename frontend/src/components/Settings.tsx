import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, PlugZap, Save, X, Globe, Cpu, PenLine } from 'lucide-react'
import {
  apiConfigDefaults,
  apiCreateProfile,
  apiDeleteProfile,
  apiErrorMessage,
  apiGetProfile,
  apiTestConfig,
  apiUpdateProfile,
} from '../api/client'
import { profileApi } from '../api/config'
import { clearOfflineSnapshot } from '../utils/offlineCache'

interface SettingsProps {
  onProfilesChanged: () => void
  onSelectProfile: (slug: string) => void
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

export default function Settings({ onProfilesChanged, onSelectProfile }: SettingsProps) {
  const [initialActive] = useState(() => profileApi.active())
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [accessToken, setAccessToken] = useState('')
  const [currentSlug, setCurrentSlug] = useState<string | null>(initialActive?.slug ?? null)
  const [mode, setMode] = useState<'create' | 'view' | 'edit'>(initialActive ? 'view' : 'create')
  const [defaultModels, setDefaultModels] = useState<Record<string, string>>({})
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<{ status: string; text: string } | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const isView = mode === 'view'
  const isEdit = mode === 'edit'
  const isCreate = mode === 'create'
  const readOnly = isView

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

  const set = (key: keyof FormState) => (value: string) =>
    setForm((f) => ({ ...f, [key]: value }))

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
      setMessage({ kind: 'err', text: apiErrorMessage(error) })
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
        await apiUpdateProfile(currentSlug, body)
        setMessage({ kind: 'ok', text: `Profile '${currentSlug}' updated.` })
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

  const removeProfile = async (slug: string) => {
    if (!confirm(`Delete profile '${slug}'? This cannot be undone.`)) return
    try {
      profileApi.setActiveSlug(slug)
      await apiDeleteProfile(slug)
      profileApi.remove(slug)
      void clearOfflineSnapshot(slug)
      refreshProfiles()
      setMessage({ kind: 'ok', text: `Profile '${slug}' deleted.` })
      setCurrentSlug(null)
      setForm(EMPTY_FORM)
      setAccessToken('')
      setMode('create')
    } catch (error) {
      setMessage({ kind: 'err', text: apiErrorMessage(error) })
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

      {isCreate && message && (
        <div className={`form-message ${message.kind}`}>{message.text}</div>
      )}

      {currentSlug && (
        <>
          <h3 className="saved-profile-heading">Saved profile (this browser)</h3>
          {message && (
            <div className={`form-message ${message.kind}`}>{message.text}</div>
          )}
          <div className="saved-profile-card">
          <button
            className="saved-profile-delete"
            aria-label="Delete profile"
            title="Delete profile"
            onClick={() => { if (currentSlug) removeProfile(currentSlug) }}
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
              <button className="saved-profile-edit" onClick={enterEdit}>
                <PenLine size={14} strokeWidth={2} />
                Edit
              </button>
            </div>
          </div>
        </div>
      </>
      )}

      {!isView && (
      <div className="settings-form">
        <h3>{isEdit ? `Edit profile: ${currentSlug}` : 'New profile'}</h3>

        <div className="form-grid">
          <label>
            Slug (identifier, lowercase + hyphens)
            <input
              value={form.slug}
              onChange={(e) => set('slug')(e.target.value.toLowerCase())}
              disabled={!isCreate}
              placeholder="e.g. acme-scm"
              maxLength={40}
            />
          </label>
          <label>
            Jira Cloud URL
            <input
              value={form.jira_cloud_url}
              onChange={(e) => set('jira_cloud_url')(e.target.value)}
              disabled={readOnly}
              placeholder="https://your-domain.atlassian.net"
            />
          </label>
          <label>
            Jira email
            <input
              value={form.jira_email}
              onChange={(e) => set('jira_email')(e.target.value)}
              disabled={readOnly}
              placeholder="you@company.com"
            />
          </label>
          <label>
            Jira API token {isEdit && <em>(blank = keep current)</em>}
            <input
              value={form.jira_api_token}
              onChange={(e) => set('jira_api_token')(e.target.value)}
              disabled={readOnly}
              placeholder="ATATT3xFfGF..."
              type="password"
              autoComplete="off"
            />
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
            LLM provider
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
            LLM API key {isEdit && <em>(blank = keep current)</em>}
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

        <p className="token-note llm-data-note">
          On Gemini's <strong>free tier</strong>, Google may use submitted prompts for product improvement —
          use a paid-tier key if that's unacceptable. For OpenRouter, prefer providers with a
          no-training / zero-retention policy. Issue text may still reach the provider (assignees are pseudonymized).
        </p>

        <p className="token-note privacy-note">
          Saving shares this workspace's sprint data (assignee names, issue summaries and descriptions) with your
          chosen AI provider for analysis. Nothing is ever written back to Jira — follow-up messages are generated for
          you to copy and paste manually. See the{' '}
          <a href="/privacy.html" target="_blank" rel="noreferrer">privacy notice</a>.
        </p>

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

        {testResult && (
          <div className={`test-result ${testResult.status === 'ok' ? 'ok' : 'partial'}`}>
            <pre>{testResult.text}</pre>
          </div>
        )}

        {isCreate && !accessToken && (
          <p className="token-note">
            An access token will be generated automatically on save and stored in this browser.
          </p>
        )}
      </div>
      )}
    </div>
  )
}
