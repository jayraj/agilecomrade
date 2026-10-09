import { useCallback, useEffect, useSyncExternalStore } from 'react'
import axios from 'axios'
import { apiSnapshot, profileApi, type Snapshot } from '../api'
import { loadOfflineSnapshot, saveOfflineSnapshot } from '../utils/offlineCache'
import { setJiraTimezone } from '../utils/format'

export interface SnapshotState {
  snapshot: Snapshot | null
  loading: boolean
  error: string | null
  noProfile: boolean
  offline: boolean
}

interface StoreState extends SnapshotState {
  lastSync: string | null
}

let current: StoreState = {
  snapshot: null,
  loading: false,
  error: null,
  noProfile: false,
  offline: false,
  lastSync: null,
}

const listeners = new Set<() => void>()

const emit = (): void => {
  listeners.forEach((listener) => listener())
}

const setStore = (patch: Partial<StoreState>): void => {
  current = { ...current, ...patch }
  emit()
}

const lastSyncListeners = new Set<(lastSync: string | null) => void>()

export const subscribeLastSync = (listener: (lastSync: string | null) => void): (() => void) => {
  lastSyncListeners.add(listener)
  listener(current.lastSync)
  return () => {
    lastSyncListeners.delete(listener)
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null
let pollIntervalMs = 0
let activeSlug: string | null = null
let inflight: Promise<void> | null = null
// Number of mounted hooks driving the shared poller. Only the last one to
// unmount stops it; this keeps polling alive while any view needs it and
// prevents one component's cleanup from cancelling another's.
let pollSubscribers = 0

const applySnapshot = (data: Snapshot): void => {
  setStore({ snapshot: data, error: null, loading: false, offline: false })
  setJiraTimezone(data.jira_timezone)
  if (data.last_sync !== current.lastSync) {
    current.lastSync = data.last_sync
    lastSyncListeners.forEach((listener) => listener(data.last_sync))
  }
}

/** Auth failures mean the profile row/token is gone server-side — not a
 *  connectivity problem, so they must not be reported as "offline". */
const authErrorMessage = (err: unknown, slug: string): string | null => {
  if (!axios.isAxiosError(err)) return null
  const status = err.response?.status
  if (status !== 401 && status !== 403) return null
  return `Profile "${slug}" was removed or its access token is no longer valid — reconnect it in Settings`
}

const doFetch = (): Promise<void> => {
  if (!activeSlug) return Promise.resolve()
  if (inflight) return inflight

  const promise = (async () => {
    try {
      const data = await apiSnapshot()
      applySnapshot(data)
      void saveOfflineSnapshot(activeSlug, data)
    } catch (err) {
      const authError = authErrorMessage(err, activeSlug)
      if (authError) {
        setStore({ error: authError, offline: false, loading: false })
        return
      }
      const cached = await loadOfflineSnapshot(activeSlug)
      if (cached) {
        setStore({
          snapshot: cached,
          error: null,
          loading: false,
          offline: true,
        })
        if (cached.last_sync && cached.last_sync !== current.lastSync) {
          current.lastSync = cached.last_sync
          lastSyncListeners.forEach((listener) => listener(cached.last_sync))
        }
      } else {
        setStore({
          error: err instanceof Error ? err.message : 'Failed to load snapshot',
          loading: false,
        })
      }
    } finally {
      inflight = null
    }
  })()

  inflight = promise
  return promise
}

const startPolling = (slug: string, intervalSeconds: number): void => {
  activeSlug = slug
  const intervalMs = Math.max(intervalSeconds, 10) * 1000
  // Reschedule when the interval changes; otherwise reuse the running timer.
  if (pollTimer && pollIntervalMs !== intervalMs) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (!pollTimer) {
    pollIntervalMs = intervalMs
    pollTimer = setInterval(() => void doFetch(), intervalMs)
  }
  if (!current.snapshot) {
    setStore({ loading: true })
  }
  void doFetch()
}

const stopPolling = (): void => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  pollIntervalMs = 0
  activeSlug = null
  inflight = null
}

export function useSnapshot(syncIntervalSeconds: number, refreshKey = 0): SnapshotState {
  const subscribe = useCallback((callback: () => void) => {
    listeners.add(callback)
    return () => {
      listeners.delete(callback)
    }
  }, [])

  const getSnapshot = useCallback(() => current, [])

  const active = profileApi.active()
  const slug = active?.slug

  useEffect(() => {
    pollSubscribers += 1
    const profiles = profileApi.list()
    const hasProfile = !!slug && profiles.some((p) => p.slug === slug)

    const onOnline = (): void => {
      void doFetch()
    }
    // Going offline won't fail until the next request; trigger one so the
    // offline badge/state reflects reality promptly instead of up to a full
    // poll interval later.
    const onOffline = (): void => {
      void doFetch()
    }

    if (!hasProfile) {
      setStore({ noProfile: true, snapshot: null, loading: false, error: null, offline: false })
      stopPolling()
    } else {
      setStore({ noProfile: false })
      startPolling(slug, syncIntervalSeconds)
      window.addEventListener('online', onOnline)
      window.addEventListener('offline', onOffline)
    }

    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
      pollSubscribers -= 1
      if (pollSubscribers <= 0) {
        pollSubscribers = 0
        stopPolling()
      }
    }
  }, [slug, syncIntervalSeconds, refreshKey])

  return useSyncExternalStore(subscribe, getSnapshot)
}
