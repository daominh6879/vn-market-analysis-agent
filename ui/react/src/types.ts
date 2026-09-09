export type Role = 'user' | 'admin'

export interface AuthState {
  user_id: string
  tenant_id: string
  role: Role
}

export interface Conversation {
  conversation_id: string
  title: string | null
  created_at: string | null
  turn_count: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  trace?: TraceStep[]
  nodePath?: string[]                 // graph nodes run for this turn, in order
  nodeMs?: Record<string, number>     // per-node latency (ms)
}

export interface TraceStep {
  agent: string
  step: string
  done: boolean
}

export interface PendingSession {
  session_id: string
  ticker: string
  status: string
  created_at: string
  expires_at: string
  risk_verdict: string | null
}

// ── Observability: graph topology + live node events ─────────────────────────

export interface GraphEdge {
  from: string
  to: string
  label: string
}

export interface GraphTopology {
  nodes: string[]
  edges: GraphEdge[]
  mermaid?: string
  error?: string
}

export type NodePhase = 'start' | 'end'

export interface NodeEvent {
  node: string
  phase: NodePhase
  ms: number
}

// ── Dashboard stats ──────────────────────────────────────────────────────────

export interface ToolStat {
  name: string
  calls: number
  errors: number
  error_rate: number
  avg_ms: number
}

export interface UsageStat {
  model: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface DashboardStats {
  turns: unknown[]
  tools: ToolStat[]
  usage_summary: UsageStat[]
  usage_daily: unknown[]
  recent_usage: unknown[]
  settings: {
    provider?: string
    model?: string
    total_turns?: number
    total_tool_calls?: number
    total_cost_usd?: number
    [k: string]: unknown
  }
  ts: number
}

// ── Langfuse trace drill-down ────────────────────────────────────────────────

export interface TraceRun {
  id: string
  name: string
  timestamp: string | null
  latency: number | null
  total_cost: number | null
  user_id: string | null
  session_id: string | null
  input: unknown
  output: unknown
  metadata: Record<string, unknown> | null
}

export interface TraceObservation {
  id: string
  parent_observation_id: string | null
  name: string
  type: string
  start_time: string | null
  end_time: string | null
  latency: number | null
  model: string | null
  usage: Record<string, unknown> | null
  cost: number | null
  level: string | null
  status: string | null
  input: unknown
  output: unknown
  metadata: Record<string, unknown> | null
}

export interface TraceRunDetail extends TraceRun {
  observations: TraceObservation[]
}
