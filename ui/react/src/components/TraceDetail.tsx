import React, { useEffect, useMemo, useState } from 'react'
import { getTraceRuns, getTraceRunDetail } from '../api'
import type { TraceRun, TraceObservation, TraceRunDetail } from '../types'

// Langfuse-backed trace drill-down: pick a run, then browse its span tree.

interface ObsNode extends TraceObservation {
  children: ObsNode[]
}

function fmtLatency(v: number | null | undefined): string {
  if (v == null) return ''
  return v >= 1 ? `${v.toFixed(2)}s` : `${Math.round(v * 1000)}ms`
}

function fmtTokens(u: Record<string, unknown> | null): string {
  if (!u) return ''
  const input = u.input ?? 0
  const output = u.output ?? 0
  const total = u.total ?? (Number(input) + Number(output))
  return `${input}→${output} (${total})`
}

function truncate(v: unknown, n = 120): string {
  if (v == null) return ''
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > n ? s.slice(0, n) + '…' : s
}

function buildTree(obs: TraceObservation[]): ObsNode[] {
  const map = new Map<string, ObsNode>()
  obs.forEach(o => map.set(o.id, { ...o, children: [] }))
  const roots: ObsNode[] = []
  obs.forEach(o => {
    const node = map.get(o.id)!
    if (o.parent_observation_id && map.has(o.parent_observation_id)) {
      map.get(o.parent_observation_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  })
  return roots
}

const TYPE_COLOR: Record<string, string> = {
  GENERATION: '#e2b96f',
  SPAN: '#10a37f',
  CHAIN: '#10a37f',
  TOOL: '#5c9cf5',
  AGENT: '#c77dfb',
}

function NodeRow({ node, depth }: { node: ObsNode; depth: number }) {
  const [open, setOpen] = useState(depth < 2)
  const color = TYPE_COLOR[node.type?.toUpperCase()] ?? '#888888'
  const hasChildren = node.children.length > 0

  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-2 py-1.5 hover:bg-[#2a2a2a] rounded text-left"
        style={{ paddingLeft: 8 + depth * 16 }}
      >
        <span className="w-3 text-[10px] text-muted">{hasChildren ? (open ? '▾' : '▸') : ''}</span>
        <span className="text-[10px] font-mono uppercase" style={{ color }}>
          {node.type}
        </span>
        <span className="text-xs text-text truncate">{node.name}</span>
        <span className="ml-auto flex items-center gap-3 flex-shrink-0">
          <span className="text-[10px] text-muted font-mono">{fmtTokens(node.usage)}</span>
          <span className="text-[10px] text-muted font-mono w-14 text-right">{fmtLatency(node.latency)}</span>
        </span>
      </button>
      {open && (
        <div className="text-[11px] text-muted pb-1" style={{ paddingLeft: 34 + depth * 16 }}>
          {node.model && <span className="mr-2 font-mono">{node.model}</span>}
          {node.status && <span className="text-red-400">{node.status}</span>}
          {node.input != null && <div className="truncate">in: {truncate(node.input)}</div>}
          {node.output != null && <div className="truncate">out: {truncate(node.output)}</div>}
        </div>
      )}
      {open &&
        node.children.map(c => <NodeRow key={c.id} node={c} depth={depth + 1} />)}
    </div>
  )
}

export default function TraceDetail() {
  const [runs, setRuns] = useState<TraceRun[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [selected, setSelected] = useState<TraceRunDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    getTraceRuns(50).then(({ runs, error }) => {
      setRuns(runs)
      if (error) setListError(error)
    })
  }, [])

  const roots = useMemo(
    () => (selected ? buildTree(selected.observations) : []),
    [selected],
  )

  async function openRun(traceId: string) {
    setLoadingDetail(true)
    const detail = await getTraceRunDetail(traceId)
    setSelected(detail)
    setLoadingDetail(false)
  }

  if (listError) {
    return (
      <div className="p-4 text-sm">
        <p className="text-red-400 mb-2">Không kết nối được Langfuse.</p>
        <p className="text-muted text-xs">{listError}</p>
        <p className="text-muted text-xs mt-1">
          Kiểm tra LANGFUSE_* trong .env rồi chạy một lượt phân tích.
        </p>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0">
      {/* run list */}
      <div className="w-64 border-r border-border overflow-y-auto flex-shrink-0">
        <p className="px-3 py-2 text-[11px] font-semibold text-muted uppercase tracking-wider border-b border-border">
          Traces ({runs.length})
        </p>
        {runs.map(r => (
          <button
            key={r.id}
            onClick={() => openRun(r.id)}
            className={`w-full text-left px-3 py-2 border-b border-border hover:bg-[#2a2a2a] transition ${
              selected?.id === r.id ? 'bg-surface' : ''
            }`}
          >
            <p className="text-xs text-text font-mono truncate">{r.name}</p>
            <p className="text-[10px] text-muted mt-0.5 truncate">
              {r.timestamp ? new Date(r.timestamp).toLocaleTimeString('vi-VN') : ''}
              {r.latency != null ? ` · ${fmtLatency(r.latency)}` : ''}
            </p>
          </button>
        ))}
        {runs.length === 0 && (
          <p className="px-3 py-4 text-xs text-muted">Chưa có trace nào.</p>
        )}
      </div>

      {/* span tree */}
      <div className="flex-1 overflow-y-auto bg-[#1a1a1a]">
        {loadingDetail ? (
          <p className="p-4 text-sm text-muted">Đang tải…</p>
        ) : selected ? (
          <div className="py-2">
            <div className="px-3 py-2 border-b border-border mb-1">
              <p className="text-sm text-text font-mono">{selected.name}</p>
              <p className="text-[10px] text-muted mt-0.5">
                {selected.latency != null ? `${fmtLatency(selected.latency)} · ` : ''}
                {selected.total_cost != null ? `$${selected.total_cost.toFixed(4)} · ` : ''}
                {selected.observations.length} observations
              </p>
            </div>
            {roots.map(r => (
              <NodeRow key={r.id} node={r} depth={0} />
            ))}
          </div>
        ) : (
          <p className="p-4 text-sm text-muted">Chọn một trace để xem cây span.</p>
        )}
      </div>
    </div>
  )
}
