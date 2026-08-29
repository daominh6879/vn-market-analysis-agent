import React, { useState, useEffect, useCallback } from 'react'
import type { AuthState, Conversation, ChatMessage, TraceStep } from './types'
import {
  getConversations,
  createConversation,
  getHistory,
  deleteConversation,
  streamMessage,
  buildTraceStep,
} from './api'
import Login from './components/Login'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'

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

  // Load conversations when auth changes
  useEffect(() => {
    if (!auth) return
    getConversations(auth.user_id, auth.tenant_id).then(setConversations)
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
  }

  function handleNew() {
    setActiveId(null)
    setMessages([])
    localStorage.removeItem(LS_CONV)
  }

  async function handleDelete(id: string) {
    await deleteConversation(id)
    if (activeId === id) {
      setActiveId(null)
      setMessages([])
      localStorage.removeItem(LS_CONV)
    }
    setConversations(prev => prev.filter(c => c.conversation_id !== id))
  }

  async function handleSelect(id: string) {
    if (id === activeId) return
    setActiveId(id)
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
      <Sidebar
        auth={auth}
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNew}
        onDelete={handleDelete}
        onLogout={handleLogout}
      />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ChatArea
          auth={auth}
          messages={messages}
          streamingMessage={streamingMsg}
          isStreaming={isStreaming}
          onSend={handleSend}
        />
      </main>
    </div>
  )
}
