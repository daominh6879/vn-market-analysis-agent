import React, { useState } from 'react'
import type { ChatMessage, GraphTopology } from '../types'
import TracePanel from './TracePanel'
import WorkflowGraph from './WorkflowGraph'

interface Props {
  message: ChatMessage | null
  isStreaming: boolean
  graphTopology: GraphTopology | null
  activeNodes: string[]
  completedNodes: string[]
}

type Tab = 'trace' | 'graph'

// Always-visible right dock (admin only): trace + graph for the selected message.
// Width is draggable via the left edge handle.
export default function TraceDock({
  message,
  isStreaming,
  graphTopology,
  activeNodes,
  completedNodes,
}: Props) {
  const [tab, setTab] = useState<Tab>('trace')
  const [width, setWidth] = useState(380)

  const nodePath = message?.nodePath ?? []
  const graphCompleted = isStreaming ? completedNodes : nodePath
  const graphActive = isStreaming ? activeNodes : []
  const hasGraph = !!graphTopology && graphTopology.nodes.length > 0 && graphCompleted.length > 0

  function startResize(e: React.MouseEvent) {
    e.preventDefault()
    const startX = e.clientX
    const startW = width
    const onMove = (ev: MouseEvent) => {
      setWidth(Math.max(280, Math.min(760, startW + (startX - ev.clientX))))
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
    }
    document.body.style.cursor = 'col-resize'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div
      style={{ width }}
      className="relative flex-shrink-0 border-l border-border bg-bg flex flex-col h-full min-w-0"
    >
      {/* resize handle */}
      <div
        onMouseDown={startResize}
        className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-accent/40 z-10"
      />

      {/* header + tabs */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-border">
        {([
          ['trace', '📋 Trace'],
          ['graph', '🕸️ Graph'],
        ] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
              tab === id ? 'bg-surface text-text' : 'text-muted hover:text-text hover:bg-surface'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* selected message query */}
      {message && (
        <div className="px-4 py-2 border-b border-border text-[11px] text-muted truncate" title={message.content}>
          {message.content.replace(/[#*`>]/g, '').slice(0, 120) || '(trống)'}
        </div>
      )}

      {/* content */}
      <div className="flex-1 overflow-y-auto min-h-0 p-3">
        {!message ? (
          <p className="text-xs text-muted">Chọn một câu trả lời để xem trace & graph.</p>
        ) : tab === 'trace' ? (
          <TracePanel steps={message.trace ?? []} streaming={isStreaming} nodePath={nodePath} />
        ) : hasGraph ? (
          <WorkflowGraph
            topology={graphTopology!}
            activeNodes={graphActive}
            completedNodes={graphCompleted}
            nodeMs={message.nodeMs ?? {}}
          />
        ) : (
          <p className="text-xs text-muted">Không có dữ liệu graph cho lượt này.</p>
        )}
      </div>
    </div>
  )
}
