import React, { useState, useEffect, useCallback } from 'react'
import type { AuthState, Conversation, ChatMessage, TraceStep } from './types'
import {
  getConversations,
  createConversation,
  getHistory,
  deleteConversation,
  streamMessage,
  buildTraceStep,
  getPendingSessions,
} from './api'
import Login from './components/Login'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import ApprovalPanel from './components/ApprovalPanel'

const LS_AUTH = 'vn_stock_auth'
const LS_CONV = 'vn_stock_conv'

function loadAuth(): AuthState | null {
  try {
    const raw = localStorage.getItem(LS_AUTH)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveAuth(auth: AuthState) {
  localStorage.setItem(LS_AUTH, JSON.stringify(auth))
}

function clearAuth() {
  localStorage.removeItem(LS_AUTH)
  localStorage.removeItem(LS_CONV)
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(loadAuth)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(
    () => localStorage.getItem(LS_CONV),
  )
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [streamingMsg, setStreamingMsg] = useState<ChatMessage | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [view, setView] = useState<'chat' | 'approvals'>('chat')
  const [pendingCount, setPendingCount] = useState(0)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Load conversations when auth changes
  useEffect(() => {
    if (!auth) return
    getConversations(auth.user_id, auth.tenant_id).then(setConversations)
    if (auth.role === 'admin') {
      getPendingSessions().then(s => setPendingCount(s.length))
    }
  }, [auth])

  // Load history when active conversation changes
  useEffect(() => {
    if (!activeId) {
      setMessages([])
      return
    }
    localStorage.setItem(LS_CONV, activeId)
    getHistory(activeId).then(setMessages)
  }, [activeId])

  function handleLogin(newAuth: AuthState) {
    saveAuth(newAuth)
    setAuth(newAuth)
    // Restore last conversation
    const savedConv = localStorage.getItem(LS_CONV)
    if (savedConv) setActiveId(savedConv)
  }

  function handleLogout() {
    clearAuth()
    setAuth(null)
    setConversations([])
    setActiveId(null)
    setMessages([])
    setSidebarOpen(false)
  }

  function handleNew() {
    setActiveId(null)
    setMessages([])
    setView('chat')
    localStorage.removeItem(LS_CONV)
    setSidebarOpen(false)
  }

  function handleApprovals() {
    setView('approvals')
    setSidebarOpen(false)
    if (auth?.role === 'admin') {
      getPendingSessions().then(s => setPendingCount(s.length))
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id)
    try {
      await deleteConversation(id)
      if (activeId === id) {
        setActiveId(null)
        setMessages([])
        localStorage.removeItem(LS_CONV)
      }
      setConversations(prev => prev.filter(c => c.conversation_id !== id))
    } finally {
      setDeletingId(null)
    }
  }

  function handleSelect(id: string) {
    if (id === activeId) return
    setActiveId(id)
    setSidebarOpen(false)
  }

  const handleSend = useCallback(async (text: string) => {
    if (!auth || isStreaming) return

    const isFirstTurn = activeId === null
    let convId = activeId

    if (isFirstTurn) {
      try {
        convId = await createConversation(auth.user_id, auth.tenant_id)
        setActiveId(convId)
        localStorage.setItem(LS_CONV, convId)
      } catch (err) {
        console.error('Failed to create conversation', err)
        return
      }
    }

    // Add user message
    const userMsg: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])

    // Prepare streaming assistant message
    let assistantContent = ''
    const traceSteps: TraceStep[] = []
    const streamMsg: ChatMessage = { role: 'assistant', content: '', trace: [] }
    setStreamingMsg({ ...streamMsg })
    setIsStreaming(true)

    await streamMessage(
      convId!,
      auth.user_id,
      auth.tenant_id,
      text,
      isFirstTurn,
      {
        onText(chunk) {
          assistantContent += chunk
          setStreamingMsg(prev => prev
            ? { ...prev, content: assistantContent, trace: [...traceSteps] }
            : null
          )
        },
        onStatus(agent, step) {
          // Add new step (not done yet), or update existing
          const existing = traceSteps.findIndex(s => s.agent === agent && s.step === step)
          if (existing === -1) {
            traceSteps.push(buildTraceStep(agent, step, false))
          }
          setStreamingMsg(prev => prev
            ? { ...prev, content: assistantContent, trace: [...traceSteps] }
            : null
          )
        },
        onDone(agent) {
          // Mark all steps for this agent as done
          traceSteps.forEach(s => {
            if (s.agent === agent) s.done = true
          })
          setStreamingMsg(prev => prev
            ? { ...prev, content: assistantContent, trace: [...traceSteps] }
            : null
          )
        },
        onError(error) {
          assistantContent += `\n\n❌ ${error}`
          setStreamingMsg(prev => prev
            ? { ...prev, content: assistantContent }
            : null
          )
        },
        onFinish() {
          const finalMsg: ChatMessage = {
            role: 'assistant',
            content: assistantContent,
            trace: [...traceSteps],
          }
          setMessages(prev => [...prev, finalMsg])
          setStreamingMsg(null)
          setIsStreaming(false)

          // Reload conversation list (title appears after first message)
          if (auth) {
            getConversations(auth.user_id, auth.tenant_id).then(setConversations)
          }
        },
      },
    )
  }, [auth, activeId, isStreaming])

  if (!auth) {
    return <Login onLogin={handleLogin} />
  }

  return (
    <div className="flex h-screen bg-bg overflow-hidden">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`fixed inset-y-0 left-0 z-40 w-64 transform transition-transform duration-200 lg:static lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <Sidebar
          auth={auth}
          conversations={conversations}
          activeId={activeId}
          view={view}
          pendingCount={pendingCount}
          deletingId={deletingId}
          onSelect={handleSelect}
          onNew={handleNew}
          onDelete={handleDelete}
          onLogout={handleLogout}
          onApprovals={handleApprovals}
        />
      </div>

      <main className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Mobile top bar */}
        <div className="lg:hidden flex items-center gap-3 px-3 py-2 border-b border-border bg-sidebar flex-shrink-0">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 rounded-lg text-muted hover:text-text hover:bg-surface transition"
            aria-label="Mở menu"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="text-sm font-semibold text-text">📈 VN Stock Chat</span>
        </div>

        {view === 'approvals' && auth.role === 'admin'
          ? <ApprovalPanel />
          : <ChatArea
              auth={auth}
              messages={messages}
              streamingMessage={streamingMsg}
              isStreaming={isStreaming}
              onSend={handleSend}
            />
        }
      </main>
    </div>
  )
}
