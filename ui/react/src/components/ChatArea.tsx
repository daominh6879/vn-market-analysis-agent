import React, { useEffect, useRef, useState } from 'react'
import type { ChatMessage, AuthState } from '../types'
import MessageBubble from './MessageBubble'
import WelcomeScreen from './WelcomeScreen'

interface Props {
  auth: AuthState
  messages: ChatMessage[]
  streamingMessage: ChatMessage | null
  isStreaming: boolean
  onSend: (text: string) => void
}

export default function ChatArea({
  auth,
  messages,
  streamingMessage,
  isStreaming,
  onSend,
}: Props) {
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isAdmin = auth.role === 'admin'
  const isEmpty = messages.length === 0 && !streamingMessage

  // Auto-scroll on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessage?.content])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
  }, [input])

  function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault()
    const text = input.trim()
    if (!text || isStreaming) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    onSend(text)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden">
      {/* Message area */}
      <div className="flex-1 overflow-y-auto">
        {isEmpty ? (
          <WelcomeScreen onSuggest={text => { onSend(text) }} />
        ) : (
          <div className="max-w-3xl mx-auto py-4">
            {messages.map((msg, i) => (
              <MessageBubble
                key={i}
                message={msg}
                isAdmin={isAdmin}
              />
            ))}
            {streamingMessage && (
              <MessageBubble
                message={streamingMessage}
                isStreaming={true}
                isAdmin={isAdmin}
              />
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="px-4 pb-4 pt-2">
        <form
          onSubmit={handleSubmit}
          className="max-w-3xl mx-auto bg-surface border border-border rounded-2xl flex items-end gap-2 px-4 py-3 focus-within:border-[#555] transition"
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Hỏi về HPG, VCB, FPT, thị trường..."
            rows={1}
            disabled={isStreaming}
            className="flex-1 bg-transparent text-text placeholder-muted text-sm resize-none focus:outline-none leading-6 max-h-48 overflow-y-auto disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim()}
            className="flex-shrink-0 w-8 h-8 rounded-lg bg-accent hover:bg-accent-hover disabled:bg-[#3d3d3d] disabled:text-muted text-white flex items-center justify-center transition self-end"
          >
            {isStreaming ? (
              <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            )}
          </button>
        </form>
        <p className="text-center text-[11px] text-muted mt-2">
          Enter gửi · Shift+Enter xuống dòng
        </p>
      </div>
    </div>
  )
}
