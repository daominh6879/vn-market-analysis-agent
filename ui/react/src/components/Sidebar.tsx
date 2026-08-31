import React from 'react'
import type { Conversation, AuthState } from '../types'
import { fmtTitle, fmtDate } from '../api'

interface Props {
  auth: AuthState
  conversations: Conversation[]
  activeId: string | null
  view: 'chat' | 'approvals'
  pendingCount: number
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onLogout: () => void
  onApprovals: () => void
}

export default function Sidebar({
  auth,
  conversations,
  activeId,
  view,
  pendingCount,
  onSelect,
  onNew,
  onDelete,
  onLogout,
  onApprovals,
}: Props) {
  return (
    <aside className="w-64 flex-shrink-0 bg-sidebar flex flex-col h-full border-r border-border">
      {/* New chat */}
      <div className="p-3 flex flex-col gap-2">
        <button
          onClick={onNew}
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm font-medium transition ${
            view === 'chat'
              ? 'bg-surface text-text'
              : 'text-text-muted hover:bg-surface hover:text-text'
          }`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Cuộc trò chuyện mới
        </button>
        {auth.role === 'admin' && (
          <button
            onClick={onApprovals}
            className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm font-medium transition ${
              view === 'approvals'
                ? 'bg-surface text-text border-accent'
                : 'text-text-muted hover:bg-surface hover:text-text'
            }`}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Phê duyệt
            {pendingCount > 0 && (
              <span className="ml-auto bg-accent text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full leading-none">
                {pendingCount}
              </span>
            )}
          </button>
        )}
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length > 0 && (
          <>
            <p className="px-2 py-1.5 text-[11px] font-semibold text-muted uppercase tracking-wider">
              Lịch sử
            </p>
            <ul className="space-y-0.5">
              {conversations.map(conv => {
                const isActive = conv.conversation_id === activeId
                return (
                  <li key={conv.conversation_id} className="group relative flex items-center">
                    <button
                      onClick={() => onSelect(conv.conversation_id)}
                      title={fmtDate(conv.created_at) + (conv.turn_count ? ` · ${conv.turn_count} lượt` : '')}
                      className={`flex-1 text-left px-3 py-2 rounded-lg text-sm truncate transition ${
                        isActive
                          ? 'bg-surface text-text border-l-2 border-accent pl-[10px]'
                          : 'text-text-muted hover:bg-[#2a2a2a] hover:text-text'
                      }`}
                    >
                      {fmtTitle(conv.title, conv.conversation_id)}
                    </button>
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        onDelete(conv.conversation_id)
                      }}
                      className="absolute right-1 p-1.5 rounded text-muted hover:text-red-400 hover:bg-red-400/10 opacity-0 group-hover:opacity-100 transition"
                      title="Xóa"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </li>
                )
              })}
            </ul>
          </>
        )}
        {conversations.length === 0 && (
          <p className="px-3 py-2 text-xs text-muted">Chưa có cuộc trò chuyện nào.</p>
        )}
      </div>

      {/* Bottom: user + logout */}
      <div className="p-3 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-7 h-7 rounded-full bg-accent/20 flex items-center justify-center text-accent text-xs font-bold flex-shrink-0">
              {auth.user_id[0]?.toUpperCase()}
            </div>
            <div className="min-w-0">
              <p className="text-sm text-text truncate leading-tight">{auth.user_id}</p>
              <p className="text-[11px] text-muted leading-tight">
                {auth.role === 'admin' ? '🔑 Admin' : '👤 User'}
              </p>
            </div>
          </div>
          <button
            onClick={onLogout}
            title="Đăng xuất"
            className="p-1.5 rounded-lg text-muted hover:text-text hover:bg-surface transition flex-shrink-0"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
          </button>
        </div>
      </div>
    </aside>
  )
}
