import React, { useState } from 'react'
import type { AuthState, Role } from '../types'

interface Props {
  onLogin: (auth: AuthState) => void
}

export default function Login({ onLogin }: Props) {
  const [userId, setUserId] = useState('')
  const [tenantId, setTenantId] = useState('default')
  const [role, setRole] = useState<Role>('user')
  const [error, setError] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!userId.trim()) {
      setError('Nhập user ID.')
      return
    }
    onLogin({ user_id: userId.trim(), tenant_id: tenantId.trim() || 'default', role })
  }

  return (
    <div className="min-h-screen bg-bg flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-[#2a2a2a] border border-border rounded-2xl p-8">
        <div className="text-center mb-7">
          <div className="text-4xl mb-3">📈</div>
          <h1 className="text-2xl font-semibold text-text tracking-tight">VN Stock Chat</h1>
          <p className="text-muted text-sm mt-1">Trợ lý phân tích tài chính chứng khoán Việt Nam</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-text-muted mb-1.5">User ID</label>
            <input
              type="text"
              value={userId}
              onChange={e => setUserId(e.target.value)}
              placeholder="vd: hung.dao"
              autoFocus
              className="w-full bg-[#1a1a1a] border border-border rounded-lg px-3 py-2.5 text-text placeholder-muted text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition"
            />
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1.5">Tenant</label>
            <input
              type="text"
              value={tenantId}
              onChange={e => setTenantId(e.target.value)}
              placeholder="default"
              className="w-full bg-[#1a1a1a] border border-border rounded-lg px-3 py-2.5 text-text placeholder-muted text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition"
            />
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1.5">Vai trò</label>
            <select
              value={role}
              onChange={e => setRole(e.target.value as Role)}
              className="w-full bg-[#1a1a1a] border border-border rounded-lg px-3 py-2.5 text-text text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition"
            >
              <option value="user">👤 User</option>
              <option value="admin">🔑 Admin</option>
            </select>
          </div>

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <button
            type="submit"
            className="w-full bg-accent hover:bg-accent-hover text-white font-semibold rounded-lg py-2.5 text-sm transition mt-2"
          >
            Bắt đầu →
          </button>
        </form>
      </div>
    </div>
  )
}
