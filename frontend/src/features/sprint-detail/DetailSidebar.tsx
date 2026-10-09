import type { ReactNode } from 'react'
import SprintDetailPanel from './SprintDetailPanel'

export interface DetailSelection {
  kind: 'active' | 'future'
  key: string
}

interface DetailSidebarProps {
  selection: DetailSelection
  onClose: () => void
}

export default function DetailSidebar({ selection, onClose }: DetailSidebarProps) {
  const content: ReactNode = (
    <SprintDetailPanel
      kind={selection.kind}
      sprintKey={decodeURIComponent(selection.key)}
      onClose={onClose}
    />
  )

  return (
    <aside className="detail-sidebar" aria-label="Sprint details">
      {content}
    </aside>
  )
}
