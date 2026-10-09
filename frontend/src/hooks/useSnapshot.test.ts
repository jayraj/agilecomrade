import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Snapshot } from '../api/client'

const { apiSnapshotMock } = vi.hoisted(() => ({ apiSnapshotMock: vi.fn() }))

vi.mock('../api/client', () => ({ apiSnapshot: apiSnapshotMock }))
vi.mock('../utils/offlineCache', () => ({
  loadOfflineSnapshot: vi.fn().mockResolvedValue(null),
  saveOfflineSnapshot: vi.fn(),
}))

import { useSnapshot } from './useSnapshot'

const snapshot = {
  last_sync: '2024-01-01T00:00:00Z',
  jira_timezone: 'UTC',
} as unknown as Snapshot

const seedProfile = (): void => {
  localStorage.setItem('srr2_profiles', JSON.stringify([{ slug: 'abc', token: 'x'.repeat(32) }]))
  localStorage.setItem('srr2_active_profile', 'abc')
}

const advance = async (ms: number): Promise<void> => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('useSnapshot polling lifecycle (B1)', () => {
  beforeEach(() => {
    apiSnapshotMock.mockReset()
    apiSnapshotMock.mockResolvedValue(snapshot)
    seedProfile()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('fetches immediately on mount', async () => {
    const { unmount } = renderHook(() => useSnapshot(10))
    await advance(0)
    expect(apiSnapshotMock).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('keeps polling on the configured interval', async () => {
    const { unmount } = renderHook(() => useSnapshot(10))
    await advance(0)
    await advance(10_000)
    expect(apiSnapshotMock).toHaveBeenCalledTimes(2)
    unmount()
  })

  it('stops polling once the last subscriber unmounts', async () => {
    const { unmount } = renderHook(() => useSnapshot(10))
    await advance(0)
    const calls = apiSnapshotMock.mock.calls.length
    unmount()
    await advance(60_000)
    expect(apiSnapshotMock.mock.calls.length).toBe(calls)
  })

  it('keeps polling while another subscriber is still mounted', async () => {
    const first = renderHook(() => useSnapshot(10))
    const second = renderHook(() => useSnapshot(10))
    await advance(0)

    first.unmount()
    const afterFirst = apiSnapshotMock.mock.calls.length
    await advance(10_000)
    expect(apiSnapshotMock.mock.calls.length).toBe(afterFirst + 1)

    second.unmount()
    const afterSecond = apiSnapshotMock.mock.calls.length
    await advance(30_000)
    expect(apiSnapshotMock.mock.calls.length).toBe(afterSecond)
  })

  it('reschedules the shared timer when a shorter interval subscribes', async () => {
    const first = renderHook(() => useSnapshot(30))
    await advance(0)

    const second = renderHook(() => useSnapshot(10))
    await advance(0)
    const beforeTick = apiSnapshotMock.mock.calls.length

    // At 10s the rescheduled timer fires once; a stacked 30s timer would not.
    await advance(10_000)
    expect(apiSnapshotMock.mock.calls.length).toBe(beforeTick + 1)

    second.unmount()
    first.unmount()
  })
})