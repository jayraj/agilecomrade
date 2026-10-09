import axios from 'axios'
import { profileApi } from './profileStore'

export const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.DEV ? 'http://127.0.0.1:5002' : '/api')

/** Debug visibility for the "🔍 View AI Prompt & Raw Response" sections.
 *  Always on in Vite dev (import.meta.env.DEV); in production builds it stays
 *  off unless VITE_SHOW_AI_DEBUG=true is explicitly set. */
export const SHOW_AI_DEBUG = import.meta.env.DEV || import.meta.env.VITE_SHOW_AI_DEBUG === 'true'

/** Feedback form URL; set VITE_FEEDBACK_URL to show the footer Feedback link. */
export const FEEDBACK_URL = import.meta.env.VITE_FEEDBACK_URL || ''

/** Extracts the backend's error message from an axios error, with a fallback. */
export const apiErrorMessage = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { error?: string } | undefined
    if (data?.error) return data.error
    if (!error.response) {
      if (error.code === 'ECONNABORTED') return error.message // request timeout
      return 'The server is unreachable. Please check your connection and try again.'
    }
    return error.message || `Request failed with status code ${error.response?.status ?? 500}`
  }
  return error instanceof Error ? error.message : 'Request failed'
}

/** HTTP status of an axios error, or undefined for non-HTTP failures. */
export const apiErrorStatus = (error: unknown): number | undefined =>
  axios.isAxiosError(error) ? error.response?.status : undefined

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 90000,
})

// Attach the active profile's slug + token to every request.
api.interceptors.request.use((config) => {
  const active = profileApi.active()
  if (active) {
    config.headers.set('X-SRR-Profile', active.slug)
    config.headers.set('X-SRR-Token', active.token)
  }
  return config
})
