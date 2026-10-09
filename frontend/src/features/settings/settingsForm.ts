export interface FormState {
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

export const EMPTY_FORM: FormState = {
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

export type ValidatedKey = 'slug' | 'jira_cloud_url' | 'jira_email' | 'jira_api_token'

export const isValidatedKey = (key: keyof FormState): key is ValidatedKey =>
  key === 'slug' || key === 'jira_cloud_url' || key === 'jira_email' || key === 'jira_api_token'

/** Mirrors the backend rules in main.py (SLUG_RE / JIRA_URL_RE) so invalid
 *  fields are flagged before any request leaves the browser. */
export function validateForm(
  form: FormState,
  isCreate: boolean,
  requireToken: boolean,
): Partial<Record<ValidatedKey, string>> {
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
