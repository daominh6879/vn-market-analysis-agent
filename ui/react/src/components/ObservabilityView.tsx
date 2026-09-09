import React, { useState } from 'react'
import type { GraphTopology } from '../types'
import DashboardView from './DashboardView'
import TraceDetail from './TraceDetail'
import WorkflowGraph from './WorkflowGraph'

type Tab = 'dashboard' | 'traces' | 'graph'

interface Props {
  topology: GraphTopology | null
  activeNodes: string[]
  completedNodes: string[]
}

const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: '📊 Dashboard' },
  { id: 'traces', label: '🔍 Traces' },
  { id: 'graph', label: '🕸️ Graph' },
]

export default function ObservabilityView({ topology, activeNodes, completedNodes }: Props) {
  const [tab, setTab] = useState<Tab>('dashboard')

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-1 px-3 py-2 border-b border-border bg-sidebar">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
              tab === t.id ? 'bg-surface text-text' : 'text-muted hover:text-text hover:bg-surface'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-hidden min-h-0">
        {tab === 'dashboard' && <div className="h-full overflow-y-auto"><DashboardView /></div>}
        {tab === 'traces' && <TraceDetail />}
        {tab === 'graph' && (
          <div className="h-full overflow-y-auto p-4">
            {topology ? (
              <WorkflowGraph
                topology={topology}
                activeNodes={activeNodes}
                completedNodes={completedNodes}
              />
            ) : (
              <p className="text-sm text-muted">Đang tải topology…</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
