import type { Conversation, ChatMessage, TraceStep, PendingSession } from './types'

const BASE = '/api'

export async function getConversations(
  user_id: string,
  tenant_id: string,
): Promise<Conversation[]> {
  const r = await fetch(
    `${BASE}/users/${encodeURIComponent(user_id)}/conversations?tenant_id=${encodeURIComponent(tenant_id)}`,
  )
  if (!r.ok) return []
  const data = await r.json()
  return data.conversations ?? []
}

export async function createConversation(
  user_id: string,
  tenant_id: string,
): Promise<string> {
  const r = await fetch(`${BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id, tenant_id }),
  })
  if (!r.ok) throw new Error(`Failed to create conversation: ${r.status}`)
  const data = await r.json()
  return data.conversation_id
}

export async function getHistory(conversation_id: string): Promise<ChatMessage[]> {
  const r = await fetch(`${BASE}/conversations/${conversation_id}/history?limit=50`)
  if (!r.ok) return []
  const data = await r.json()
  return (data.messages ?? []) as ChatMessage[]
}

export async function deleteConversation(conversation_id: string): Promise<void> {
  await fetch(`${BASE}/conversations/${conversation_id}`, { method: 'DELETE' })
}

export interface StreamCallbacks {
  onText: (text: string) => void
  onStatus: (agent: string, step: string) => void
  onDone: (agent: string) => void
  onError: (error: string) => void
  onFinish: () => void
}

export async function streamMessage(
  conversation_id: string,
  user_id: string,
  tenant_id: string,
  message: string,
  is_first_turn: boolean,
  callbacks: StreamCallbacks,
): Promise<void> {
  const response = await fetch(
    `${BASE}/conversations/${conversation_id}/messages/stream`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ user_id, tenant_id, message, is_first_turn }),
    },
  )

  if (!response.ok || !response.body) {
    callbacks.onError(`HTTP ${response.status}`)
    callbacks.onFinish()
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const payload = JSON.parse(line.slice(6))
            if ('text' in payload) {
              callbacks.onText(payload.text)
            } else if (currentEvent === 'status') {
              callbacks.onStatus(payload.agent ?? '', payload.step ?? '')
            } else if (currentEvent === 'done') {
              callbacks.onDone(payload.agent ?? '')
            } else if (currentEvent === 'error') {
              callbacks.onError(payload.error ?? 'Unknown error')
            }
          } catch {
            // ignore malformed JSON
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
    callbacks.onFinish()
  }
}

export const AGENT_LABELS: Record<string, string> = {
  price_action: '📊 Dòng tiền',
  technical_analysis: '📈 Kỹ thuật',
  fundamentals: '🏦 Tài chính cơ bản',
  macro_sector: '🌐 Vĩ mô / Ngành',
  news_sentiment: '📰 Tin tức',
  screening: '🔍 Lọc cổ phiếu',
  market_brief: '🌏 Thị trường',
  qa_document: '📄 Tài liệu',
  conversation: '💬 Hội thoại',
}

export const STEP_LABELS: Record<string, string> = {
  loading_history: 'Đang tải lịch sử',
  routing: 'Đang phân tích câu hỏi',
  collecting_data: 'Đang thu thập dữ liệu giá',
  collecting_market_data: 'Đang thu thập dữ liệu thị trường',
  collecting_macro_data: 'Đang thu thập dữ liệu vĩ mô',
  fetching_news: 'Đang lấy tin tức',
  querying_documents: 'Đang tìm kiếm tài liệu',
  streaming: 'Đang soạn câu trả lời',
}

export function formatTraceLabel(agent: string, step: string): string {
  const a = AGENT_LABELS[agent] ?? agent
  const s = STEP_LABELS[step] ?? step
  return agent ? `${a} · ${s}` : s
}

export function fmtDate(created_at: string | null): string {
  if (!created_at) return ''
  try {
    const d = new Date(created_at)
    return d.toLocaleString('vi-VN', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

export function fmtTitle(title: string | null, id: string): string {
  if (!title) return `Cuộc trò chuyện #${id.slice(0, 6)}`
  return title.length > 42 ? title.slice(0, 42) + '…' : title
}

export function buildTraceStep(agent: string, step: string, done: boolean): TraceStep {
  return { agent, step, done }
}

// --- Sessions (human approval) ---

export async function getPendingSessions(): Promise<PendingSession[]> {
  const r = await fetch(`${BASE}/sessions/pending`)
  if (!r.ok) return []
  const data = await r.json()
  return data.sessions ?? []
}

export async function approveSession(
  session_id: string,
): Promise<{ session_id: string; ticker: string; risk_verdict: string; report: string }> {
  const r = await fetch(`${BASE}/sessions/${session_id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  })
  if (!r.ok) throw new Error(`Approve failed: ${r.status}`)
  return r.json()
}

export async function rejectSession(session_id: string): Promise<void> {
  const r = await fetch(`${BASE}/sessions/${session_id}/reject`, { method: 'POST' })
  if (!r.ok) throw new Error(`Reject failed: ${r.status}`)
}
