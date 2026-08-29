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
}

export interface TraceStep {
  agent: string
  step: string
  done: boolean
}
