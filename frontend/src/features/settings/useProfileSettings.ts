import { useEffect, useState } from 'react'
import {
  apiConfigDefaults,
  apiCreateProfile,
  apiDeleteProfile,
  apiErrorMessage,
  apiErrorStatus,
  apiGetProfile,
  apiTestConfig,
  apiUpdateProfile,
  profileApi,
} from '../../api'
import { clearOfflineSnapshot } from '../../utils/offlineCache'
import { EMPTY_FORM, isValidatedKey, validateForm, type FormState, type ValidatedKey } from './settingsForm'

export interface ProfileSettings {
  form: FormState
  currentSlug: string | null
  mode: 'create' | 'view' | 'edit'
  defaultModels: Record<string, string>
  testing: boolean
  saving: boolean
  deleting: boolean
  confirmDelete: boolean
  testResult: { status: string; text: string } | null
  message: { kind: 'ok' | 'err'; text: string } | null
  fieldErrors: Partial<Record<ValidatedKey, string>>
  fieldErrorList: string[]
  isView: boolean
  isEdit: boolean
  isCreate: boolean
  readOnly: boolean
  set: (key: keyof FormState) => (value: string) => void
  switchProvider: (provider: string) => void
  enterEdit: () => void
  cancelEdit: () => void
  testConnection: () => Promise<void>
  saveProfile: () => Promise<void>
  startDelete: () => void
  cancelDelete: () => void
  confirmDeleteNow: (slug: string) => Promise<void>
}

/**
 * All profile settings form state and API orchestration, extracted from the
 * Settings page so the component stays presentational.
 */
export const useProfileSettings = (
  onSelectProfile: (slug: string | null) => void,
): ProfileSettings => {
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

  return {
    form,
    currentSlug,
    mode,
    defaultModels,
    testing,
    saving,
    deleting,
    confirmDelete,
    testResult,
    message,
    fieldErrors,
    fieldErrorList,
    isView,
    isEdit,
    isCreate,
    readOnly,
    set,
    switchProvider,
    enterEdit,
    cancelEdit,
    testConnection,
    saveProfile,
    startDelete,
    cancelDelete,
    confirmDeleteNow,
  }
}
