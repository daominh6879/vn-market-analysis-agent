import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { ChatMessage } from '../types'
import TracePanel from './TracePanel'

interface Props {
  message: ChatMessage
  isStreaming?: boolean
  isAdmin: boolean
}

export default function MessageBubble({ message, isStreaming = false, isAdmin }: Props) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="flex justify-end px-4 py-3">
        <div className="max-w-[75%] bg-surface rounded-2xl px-4 py-3 text-text text-sm leading-6">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-4">
      <div className="flex gap-4 max-w-3xl mx-auto">
        {/* Avatar */}
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-accent/20 flex items-center justify-center text-sm mt-0.5">
          📈
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {isStreaming && !message.content ? (
            <div className="flex items-center gap-2 text-muted text-sm py-1">
              <span className="thinking-dot" />
              <span>Đang soạn câu trả lời...</span>
            </div>
          ) : (
            <div className="prose-chat text-sm">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  code({ node, className, children, ...props }) {
                    const match = /language-(\w+)/.exec(className || '')
                    const isBlock = match !== null
                    if (isBlock) {
                      return (
                        <SyntaxHighlighter
                          style={oneDark as Record<string, React.CSSProperties>}
                          language={match[1]}
                          PreTag="div"
                          customStyle={{
                            background: '#1a1a1a',
                            borderRadius: '10px',
                            border: '1px solid #3d3d3d',
                            fontSize: '0.82rem',
                            margin: '0.75rem 0',
                          }}
                        >
                          {String(children).replace(/\n$/, '')}
                        </SyntaxHighlighter>
                      )
                    }
                    return (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    )
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}

          {isStreaming && message.content && (
            <span className="inline-block w-0.5 h-4 bg-text-muted ml-0.5 animate-pulse" />
          )}

          {/* Admin trace */}
          {isAdmin && (
            <TracePanel
              steps={message.trace ?? []}
              streaming={isStreaming}
            />
          )}
        </div>
      </div>
    </div>
  )
}
