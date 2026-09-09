import React, { useId, useMemo } from 'react'
import type { GraphTopology } from '../types'

// Data-driven SVG topology chart (vertical: START top → END bottom). Renders the
// LangGraph topology from /api/graph and lights the run path from node events.

interface Props {
  topology: GraphTopology
  activeNodes: string[]       // nodes currently running
  completedNodes: string[]    // nodes completed, in execution order
  nodeMs?: Record<string, number>   // per-node latency (ms)
}

interface Edge {
  from: string
  to: string
  label: string
}

interface Pos {
  x: number
  y: number
}

const NODE_SUB: Record<string, string> = {
  classify_node: 'classify',
  clarify_node: 'clarify · interrupt',
  node_market_brief: 'market brief',
  node_out_of_scope: 'out of scope',
  build_single_subtask_node: 'build subtask',
  decompose_node: 'decompose',
  run_subqueries_node: 'gather data',
  synthesize_final: 'LLM synthesis',
  critique_report_node: 'self-review',
  finalize_node: 'save + reply',
}

const START = 'START'
const END = 'END'

const W = 150
const H = 48
const GX = 60   // horizontal gap between sibling nodes in a row
const GY = 34   // vertical gap between rows
const PAD = 16

// Conditional edges whose label means "loop back and redo" — these are cycle
// edges (replan/retry), not forward flow, so they're excluded from layering.
const LOOP_LABELS = new Set(['replan', 'retry', 'repeat', 'redo', 'again'])

function normalizeEdges(edges: Edge[]): Edge[] {
  return edges.map(e => ({
    from: e.from === '__start__' ? START : e.from,
    to: e.to === '__end__' ? END : e.to,
    label: e.label || '',
  }))
}

function isBackEdge(e: Edge): boolean {
  return LOOP_LABELS.has((e.label || '').toLowerCase())
}

function layout(topology: GraphTopology) {
  const names = [START, ...topology.nodes, END]
  const edges = normalizeEdges(topology.edges)

  // Longest-path layering over the DAG formed by dropping loop (back) edges.
  // A plain BFS shortest-path mis-ranks the graph sink (finalize_node, reachable
  // via the short "conversation" edge) mid-graph; longest-path puts it last.
  const dagEdges = edges.filter(e => !isBackEdge(e))
  const layer: Record<string, number> = {}
  names.forEach(n => { layer[n] = 0 })
  for (let pass = 0; pass < names.length; pass++) {
    for (const e of dagEdges) {
      layer[e.to] = Math.max(layer[e.to] ?? 0, (layer[e.from] ?? 0) + 1)
    }
  }
  layer[START] = 0
  let maxLayer = 0
  names.forEach(n => {
    if (n !== END) maxLayer = Math.max(maxLayer, layer[n])
  })
  layer[END] = maxLayer + 1

  // group by layer into top-to-bottom rows
  const rows: string[][] = []
  names.forEach(n => {
    const r = layer[n] ?? 0
    ;(rows[r] = rows[r] || []).push(n)
  })

  const maxRow = Math.max(...rows.map(r => r.length))
  const width = maxRow * (W + GX) - GX + PAD * 2
  const height = rows.length * (H + GY) - GY + PAD * 2

  const pos: Record<string, Pos> = {}
  rows.forEach((row, ri) => {
    const rowW = row.length * (W + GX) - GX
    row.forEach((n, ci) => {
      pos[n] = {
        x: PAD + (width - PAD * 2 - rowW) / 2 + ci * (W + GX),
        y: PAD + ri * (H + GY),
      }
    })
  })

  return { names, edges, pos, width, height }
}

export default function WorkflowGraph({ topology, activeNodes, completedNodes, nodeMs = {} }: Props) {
  const arrowId = useId().replace(/:/g, '')
  const { names, edges, pos, width, height } = useMemo(
    () => layout(topology),
    [topology],
  )

  if (!topology.nodes.length) {
    return (
      <div className="text-xs text-muted text-center py-6">
        {topology.error || 'No graph topology — is the backend running?'}
      </div>
    )
  }

  const activeSet = new Set(activeNodes)

  // completed path, bounded by START/END so the highlight runs the full flow
  const path = completedNodes.length > 0 ? [START, ...completedNodes, END] : []
  const doneSet = new Set(path)
  const traversedEdges = new Set<string>()
  for (let i = 1; i < path.length; i++) {
    traversedEdges.add(`${path[i - 1]}~${path[i]}`)
  }

  const edgePath = (e: Edge) => {
    const a = pos[e.from]
    const b = pos[e.to]
    if (!a || !b) return null
    const x1 = a.x + W / 2
    const y1 = a.y + H
    const x2 = b.x + W / 2
    const y2 = b.y
    const my = (y1 + y2) / 2
    return { d: `M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`, mx: (x1 + x2) / 2, my }
  }

  return (
    <div className="overflow-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ minWidth: width, minHeight: height }}
        className="graph-chart"
        role="img"
        aria-label="workflow graph"
      >
        <defs>
          <marker
            id={arrowId}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0 0 L10 5 L0 10 z" fill="#3d3d3d" />
          </marker>
        </defs>

        {/* edges */}
        {edges.map((e, i) => {
          const p = edgePath(e)
          if (!p) return null
          const key = `${e.from}~${e.to}`
          const isTraversed = traversedEdges.has(key)
          const conditional = !!e.label
          const stroke = isTraversed ? '#10a37f' : '#3d3d3d'
          return (
            <g key={i}>
              <path
                d={p.d}
                fill="none"
                stroke={stroke}
                strokeWidth={isTraversed ? 2 : 1.4}
                strokeDasharray={conditional ? '5 4' : undefined}
                markerEnd={`url(#${arrowId})`}
              />
              {conditional && (
                <text
                  x={p.mx + 8}
                  y={p.my + 3}
                  fontSize="9"
                  fill="#888888"
                >
                  {e.label}
                </text>
              )}
            </g>
          )
        })}

        {/* nodes */}
        {names.map(n => {
          const p = pos[n]
          const isStartEnd = n === START || n === END
          const isActive = activeSet.has(n)
          const isDone = doneSet.has(n)
          const sub = NODE_SUB[n] || ''

          if (isStartEnd) {
            const fill = isDone ? '#1f4d42' : '#171717'
            return (
              <g key={n}>
                <rect
                  x={p.x}
                  y={p.y}
                  width={W}
                  height={H}
                  rx={9}
                  fill={fill}
                  stroke={isDone ? '#10a37f' : '#3d3d3d'}
                />
                <text
                  x={p.x + W / 2}
                  y={p.y + H / 2 + 4}
                  textAnchor="middle"
                  fontSize="11"
                  fill="#888888"
                >
                  {n}
                </text>
              </g>
            )
          }

          const fill = isActive ? 'rgba(16,163,127,0.28)' : isDone ? '#1f4d42' : '#2f2f2f'
          const stroke = isActive ? '#10a37f' : isDone ? '#10a37f' : '#3d3d3d'
          return (
            <g key={n} className={isActive ? 'graph-node-active' : undefined}>
              <rect
                x={p.x}
                y={p.y}
                width={W}
                height={H}
                rx={9}
                fill={fill}
                stroke={stroke}
                strokeWidth={isActive ? 1.6 : 1}
              />
              <text x={p.x + 12} y={p.y + 21} fontSize="11" fontWeight={600} fill="#ececec">
                {n}
              </text>
              {sub && (
                <text x={p.x + 12} y={p.y + 37} fontSize="9" fill="#888888">
                  {sub}
                </text>
              )}
              {isDone && (
                <text x={p.x + W - 14} y={p.y + 21} fontSize="11" fill="#10a37f">
                  ✓
                </text>
              )}
              {isDone && nodeMs[n] != null && (
                <text x={p.x + W - 8} y={p.y + H - 8} fontSize="8" fill="#888888" textAnchor="end">
                  {nodeMs[n]}ms
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
