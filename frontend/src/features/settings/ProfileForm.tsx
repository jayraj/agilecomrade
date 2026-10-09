import { PlugZap, Save, X } from 'lucide-react'
import type { FormState, ValidatedKey } from './settingsForm'

interface ProfileFormProps {
  form: FormState
  currentSlug: string | null
  isCreate: boolean
  isEdit: boolean
  isView: boolean
  readOnly: boolean
  defaultModels: Record<string, string>
  fieldErrors: Partial<Record<ValidatedKey, string>>
  fieldErrorList: string[]
  message: { kind: 'ok' | 'err'; text: string } | null
  testing: boolean
  saving: boolean
  testResult: { status: string; text: string } | null
  onFieldChange: (key: keyof FormState) => (value: string) => void
  onSwitchProvider: (provider: string) => void
  onTestConnection: () => void
  onSave: () => void
  onCancelEdit: () => void
}

export default function ProfileForm({
  form,
  currentSlug,
  isCreate,
  isEdit,
  isView,
  readOnly,
  defaultModels,
  fieldErrors,
  fieldErrorList,
  message,
  testing,
  saving,
  testResult,
  onFieldChange,
  onSwitchProvider,
  onTestConnection,
  onSave,
  onCancelEdit,
}: ProfileFormProps) {
  return (
    <div className="settings-form">
      <h3>{isEdit ? `Edit profile: ${currentSlug}` : 'New profile'}</h3>

      <div className="form-grid">
        <label className={fieldErrors.slug ? 'field-error' : undefined}>
          Slug (identifier, lowercase + hyphens)
          <input
            value={form.slug}
            onChange={(e) => onFieldChange('slug')(e.target.value.toLowerCase())}
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
            onChange={(e) => onFieldChange('jira_cloud_url')(e.target.value)}
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
            onChange={(e) => onFieldChange('jira_email')(e.target.value)}
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
            onChange={(e) => onFieldChange('jira_api_token')(e.target.value)}
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
            onChange={(e) => onFieldChange('jira_projects')(e.target.value)}
            disabled={readOnly}
            placeholder="PFIN, MOS"
          />
        </label>
        <label>
          Story points field (optional — blank auto-detects)
          <input
            value={form.story_points_field}
            onChange={(e) => onFieldChange('story_points_field')(e.target.value)}
            disabled={readOnly}
            placeholder="customfield_10102"
          />
        </label>
      </div>

      <div className="form-grid">
        <label>
          LLM provider <em>(optional — skip for rule-based only)</em>
          <select value={form.llm_provider} onChange={(e) => onSwitchProvider(e.target.value)} disabled={readOnly}>
            <option value="gemini">Gemini</option>
            <option value="openrouter">OpenRouter</option>
          </select>
        </label>
        <label>
          Model
          <input
            value={form.llm_model}
            onChange={(e) => onFieldChange('llm_model')(e.target.value)}
            disabled={readOnly}
            placeholder={defaultModels[form.llm_provider] || 'gemini-flash-latest'}
          />
        </label>
        <label className="form-full">
          LLM API key {isEdit ? <em>(blank = keep current)</em> : <em>(optional — blank disables AI analysis)</em>}
          <input
            value={form.llm_api_key}
            onChange={(e) => onFieldChange('llm_api_key')(e.target.value)}
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
          <button className="settings-btn" onClick={onTestConnection} disabled={testing || saving}>
            <PlugZap size={16} strokeWidth={2} />
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
        )}
        {!isView && (
          <button className="settings-btn-primary" onClick={onSave} disabled={saving || testing}>
            <Save size={16} strokeWidth={2} />
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}
        {isEdit && (
          <button className="settings-btn-danger" onClick={onCancelEdit}>
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
  )
}
