import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Snapshot } from '../../api/client'

const { snapshotRef } = vi.hoisted(() => ({
  snapshotRef: { current: null as Snapshot | null },
}))

vi.mock('../../context/SyncContext', () => ({
  useSync: () => ({ syncIntervalSeconds: 300, refreshKey: 0 }),
}))

vi.mock('../../hooks/useSnapshot', () => ({
  useSnapshot: () => ({
    snapshot: snapshotRef.current,
    loading: false,
    error: null,
    noProfile: false,
    offline: false,
  }),
}))

vi.mock('../../api/client', () => ({
  SHOW_AI_DEBUG: false,
  apiGenerateFollowup: vi.fn(),
  apiGenerateMitigations: vi.fn(),
  apiNextSprintIssues: vi.fn(),
  apiNextSprintRisks: vi.fn(),
  apiSetRiskDecision: vi.fn(),
}))

vi.mock('../dashboard/SprintGauge', () => ({ default: () => null }))
vi.mock('./RiskCardItem', () => ({ default: () => null }))
vi.mock('./RiskDetailMatrix', () => ({ default: () => null }))
vi.mock('./WorkItemTable', () => ({ default: () => null }))

import SprintDetailPanel from './SprintDetailPanel'

const makeSnapshot = (mitigations: unknown[]): Snapshot =>
  ({
    last_sync: '2024-01-01T00:00:00Z',
    jira_timezone: 'UTC',
    blockers: [],
    sprint_data: {},
    mitigations,
    radar_data: [],
  }) as unknown as Snapshot

describe('SprintDetailPanel persisted mitigation plan (B2)', () => {
  beforeEach(() => {
    snapshotRef.current = null
  })

  it('renders a saved plan on load without regenerating it', () => {
    snapshotRef.current = makeSnapshot([
      {
        sprint_key: 'Sprint 1',
        ai_used: true,
        action_items: ['Re-balance scope'],
        success_criteria: [],
      },
    ])

    render(<SprintDetailPanel kind="active" sprintKey="Sprint 1" />)

    expect(screen.getByText('AI MITIGATION PLAN')).toBeTruthy()
    expect(screen.getByText('Re-balance scope')).toBeTruthy()
  })

  it('hides the plan when the snapshot has no saved plan for the sprint', () => {
    snapshotRef.current = makeSnapshot([])

    render(<SprintDetailPanel kind="active" sprintKey="Sprint 1" />)

    expect(screen.queryByText('AI MITIGATION PLAN')).toBeNull()
  })
})